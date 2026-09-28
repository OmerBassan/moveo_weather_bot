"""Risk-change detection: re-score every hub, diff against the last run, report.

    python -m scripts.check_risk_changes            # detect and print
    python -m scripts.check_risk_changes --webhook https://...

THIS FEATURE IS ONLY COHERENT BECAUSE THE SCORES ARE DETERMINISTIC.

"Alert when a hub's risk score changes" presumes that a score which did not
change stays identical. That holds here: a score is a pure function of the
frozen snapshots, the live NWS alerts and the live forecast, computed by
`engine.py`, which no language model touches. Run it twice in calm weather and
every one of the 120 scores is byte-identical, so a diff means something
happened.

Had the model produced the number, every run would differ slightly and every
run would look like a change -- the feature would be noise generating noise.
This is the clearest practical payoff of keeping the LLM out of the arithmetic,
and it is why the bonus was worth building rather than bolted on.

WHAT COUNTS AS A CHANGE is a judgement, so it is configurable and stated:
  - a score moving by at least `min_delta` points, or
  - a band crossing (Moderate -> High), which matters even when small, because
    bands are what an investment conversation actually uses.

A band crossing is reported even below `min_delta`: crossing 60.0 is the whole
point of having a boundary there.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import AppConfig, load_config
from app.hubs import load_hubs
from app.scoring.engine import HAZARDS, Hazard, rank_hubs
from app.tools import nws

logger = logging.getLogger(__name__)

# Points. Below this a move is reported only if it also crossed a band.
# Forecast values shift hour to hour, so a small threshold would page someone
# about weather that has not arrived.
DEFAULT_MIN_DELTA = 3.0


@dataclass(frozen=True)
class RiskChange:
    hub_id: str
    hub: str
    hazard: str
    previous_score: float
    current_score: float
    previous_band: str
    current_band: str
    drivers: tuple[str, ...]
    active_alerts: tuple[str, ...]

    @property
    def delta(self) -> float:
        return round(self.current_score - self.previous_score, 1)

    @property
    def band_changed(self) -> bool:
        return self.previous_band != self.current_band

    @property
    def direction(self) -> str:
        return "increased" if self.delta > 0 else "decreased"

    def headline(self) -> str:
        line = (
            f"{self.hub} — {self.hazard} risk {self.direction} "
            f"{abs(self.delta):.1f} points to {self.current_score:.1f}"
        )
        if self.band_changed:
            line += f" ({self.previous_band} → {self.current_band})"
        return line

    def as_dict(self) -> dict[str, Any]:
        return {
            "hub_id": self.hub_id,
            "hub": self.hub,
            "hazard": self.hazard,
            "previous_score": self.previous_score,
            "current_score": self.current_score,
            "delta": self.delta,
            "previous_band": self.previous_band,
            "current_band": self.current_band,
            "band_changed": self.band_changed,
            "headline": self.headline(),
            "drivers": list(self.drivers),
            "active_alerts": list(self.active_alerts),
        }


@dataclass
class RiskSnapshot:
    """Every hub-hazard score at one moment."""

    taken_at: str
    scores: dict[str, dict[str, Any]] = field(default_factory=dict)

    @staticmethod
    def key(hub_id: str, hazard: str) -> str:
        return f"{hub_id}:{hazard}"

    def as_dict(self) -> dict[str, Any]:
        return {"taken_at": self.taken_at, "scores": self.scores}

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RiskSnapshot:
        return cls(taken_at=payload.get("taken_at", ""), scores=payload.get("scores", {}))


def state_path(config: AppConfig | None = None) -> Path:
    config = config or load_config()
    return config.hubs_path.parent / "risk_state.json"


def load_previous(config: AppConfig | None = None) -> RiskSnapshot | None:
    path = state_path(config)
    if not path.exists():
        return None
    return RiskSnapshot.from_dict(json.loads(path.read_text(encoding="utf-8")))


def save(snapshot: RiskSnapshot, config: AppConfig | None = None) -> Path:
    path = state_path(config)
    path.write_text(json.dumps(snapshot.as_dict(), indent=2), encoding="utf-8")
    return path


def take_snapshot(
    config: AppConfig | None = None,
    alerts: dict[str, tuple[nws.Alert, ...]] | None = None,
    forecasts: dict[str, dict[str, Any]] | None = None,
) -> RiskSnapshot:
    """Score every hub for every hazard, from live alerts and forecasts.

    One alert fetch and one forecast fetch for the whole run, shared across all
    three hazards -- otherwise the three rankings could disagree about the
    current weather, and a 'change' could be an artefact of fetch ordering.

    `alerts` and `forecasts` ACCEPT ALREADY-FETCHED DATA so that a caller which
    has some can avoid fetching it twice. That matters for the agent tool: a
    chat turn has already fetched alerts once, and fetching them again here
    would let the tool's answer and the same turn's scores describe different
    weather -- the exact failure that one-fetch-per-turn in `Deps` prevents.
    Both default to fetching, so the scheduled script and the API endpoint are
    unchanged.
    """
    config = config or load_config()
    nri = json.loads(config.nri_snapshot_path.read_text(encoding="utf-8"))
    registry = load_hubs()

    alerts = nws.fetch_active_alerts(config) if alerts is None else alerts
    engine_alerts = nws.alerts_as_engine_input(alerts)
    if forecasts is None:
        forecasts = {
            hub_id: forecast.as_dict()
            for hub_id, forecast in nws.fetch_forecasts(registry.hubs, config).items()
        }

    snapshot = RiskSnapshot(taken_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    for hazard in HAZARDS:
        for result in rank_hubs(
            registry.hubs, hazard, nri, engine_alerts, forecasts_by_hub=forecasts
        ):
            driver = result.top_driver
            snapshot.scores[RiskSnapshot.key(result.hub_id, hazard)] = {
                "hub_id": result.hub_id,
                "hub": result.hub_label,
                "hazard": hazard,
                "score": result.score,
                "band": result.band,
                "top_driver": driver.name if driver else None,
                "components": list(result.breakdown()),
                "active_alerts": [
                    f"{a.event} ({a.severity})" for a in alerts.get(result.hub_id, ())
                ],
            }
    return snapshot


def diff(
    previous: RiskSnapshot, current: RiskSnapshot, min_delta: float = DEFAULT_MIN_DELTA
) -> list[RiskChange]:
    """Changes worth reporting, largest absolute move first."""
    changes: list[RiskChange] = []

    for key, now in current.scores.items():
        before = previous.scores.get(key)
        if before is None:
            # A newly added hub or hazard is not a risk change; it has no
            # previous value to have moved from. Reporting it would make every
            # registry edit look like a weather event.
            continue

        delta = now["score"] - before["score"]
        band_changed = now["band"] != before["band"]
        if abs(delta) < min_delta and not band_changed:
            continue

        changes.append(
            RiskChange(
                hub_id=now["hub_id"],
                hub=now["hub"],
                hazard=now["hazard"],
                previous_score=before["score"],
                current_score=now["score"],
                previous_band=before["band"],
                current_band=now["band"],
                drivers=tuple(now.get("components", ())),
                active_alerts=tuple(now.get("active_alerts", ())),
            )
        )

    return sorted(changes, key=lambda c: (-abs(c.delta), c.hub_id))


def render(changes: list[RiskChange], previous_at: str, current_at: str) -> str:
    if not changes:
        return f"No risk changes between {previous_at} and {current_at}."

    crossings = [c for c in changes if c.band_changed]
    lines = [
        f"{len(changes)} risk change(s) between {previous_at} and {current_at}"
        + (f", including {len(crossings)} band crossing(s)" if crossings else ""),
        "",
    ]
    for change in changes:
        lines.append(f"  {'!' if change.band_changed else '-'} {change.headline()}")
        for alert in change.active_alerts[:3]:
            lines.append(f"      active: {alert}")
    return "\n".join(lines)


def webhook_payload(
    changes: list[RiskChange], previous: RiskSnapshot, current: RiskSnapshot
) -> dict[str, Any]:
    """The POST body. Same disclosure discipline as a chat answer: the
    thresholds that decided what counts as a change travel with the changes."""
    return {
        "event": "weather_risk_changed",
        "detected_at": current.taken_at,
        "compared_against": previous.taken_at,
        "change_count": len(changes),
        "band_crossings": sum(1 for c in changes if c.band_changed),
        "changes": [c.as_dict() for c in changes],
        "assumptions": [
            "Scores are produced by a deterministic engine; an unchanged score is "
            "byte-identical between runs, so any change here reflects changed "
            "inputs rather than model variation.",
            "A change is reported when a score moves by at least the configured "
            "threshold, or when it crosses a band boundary at any size.",
        ],
        "sources": [
            "FEMA National Risk Index v1.20.0 (December 2025)",
            "NWS active alerts",
            "NWS quantitative gridpoint forecast",
            "Open-Meteo ECMWF IFS reanalysis",
        ],
    }
