"""The typed tools the agent may call.

Each one normalises its upstream before the model sees it, and none returns a
raw API payload. Two consequences that matter:

  - the model cannot quote a field nobody validated;
  - a tool result is small enough to stay in context across a conversation,
    so follow-up questions do not force a refetch.

`rank_hubs_by_risk` is the important one. The agent calls it, but it cannot
influence it: the ranking and every score are computed by the deterministic
engine from frozen data plus live alerts. The agent's contribution is deciding
WHICH hubs and WHICH hazard to ask about -- and then explaining what came back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.config import (
    SNOW_DAY_DISRUPTION_THRESHOLD_IN,
    SNOW_DAY_LITERAL_THRESHOLD_IN,
)
from app.hubs import Hub, HubRegistry, load_hubs
from app.scoring import climatology
from app.scoring.engine import HAZARDS, Hazard, HazardScore, rank_hubs, score_hub
from app.tools import nws

logger = logging.getLogger(__name__)

# What `count_weather_days` accepts, mapped to (variable, default threshold,
# strict). Named metrics rather than a free variable+threshold pair, so the
# model cannot invent a threshold and present the result as a measurement.
MEASUREMENT_METRICS: dict[str, tuple[str, float, bool, str]] = {
    "any_snowfall": (
        climatology.SNOWFALL, SNOW_DAY_LITERAL_THRESHOLD_IN, True,
        "days with any recorded snowfall at all",
    ),
    "disruptive_snowfall": (
        climatology.SNOWFALL, SNOW_DAY_DISRUPTION_THRESHOLD_IN, False,
        f"days with at least {SNOW_DAY_DISRUPTION_THRESHOLD_IN} inch of fresh snow",
    ),
    "heavy_precipitation": (
        climatology.PRECIPITATION, 1.0, False,
        "days with at least 1 inch of precipitation",
    ),
    "damaging_wind": (
        climatology.WIND_GUST_MAX, 58.0, False,
        "days with wind gusts at or above 58 mph",
    ),
    "freezing": (
        climatology.TEMP_MIN, 32.0, False,
        "days whose minimum temperature reached 32F or below",
    ),
}


@dataclass
class Deps:
    """Everything the tools need, built once per request.

    Alerts are fetched ONCE and shared across every tool call in the turn. A
    per-tool fetch would let two tools in the same answer disagree about the
    current weather.
    """

    nri: dict[str, Any]
    registry: HubRegistry = field(default_factory=load_hubs)
    alerts: dict[str, tuple[nws.Alert, ...]] = field(default_factory=dict)
    alerts_error: str | None = None
    sources_used: set[str] = field(default_factory=set)

    def engine_alerts(self) -> dict[str, tuple[dict[str, Any], ...]]:
        return nws.alerts_as_engine_input(self.alerts)

    def hub_or_error(self, hub_id: str) -> Hub | str:
        hub = self.registry.by_id(hub_id)
        if hub is not None:
            return hub
        near = [h.id for h in self.registry.hubs if hub_id.split("-")[0] in h.id]
        suggestion = f" Did you mean: {', '.join(near)}?" if near else ""
        return (
            f"No hub with id {hub_id!r} is in this network. Call list_hubs to see "
            f"the {len(self.registry.hubs)} hubs that exist.{suggestion}"
        )


def _assessment_payload(result: HazardScore, rank: int | None, deps: Deps) -> dict[str, Any]:
    alerts = deps.alerts.get(result.hub_id, ())
    return {
        "hub_id": result.hub_id,
        "hub": result.hub_label,
        "hazard": result.hazard,
        "risk_score": result.score,
        "risk_band": result.band,
        "rank": rank,
        "top_driver": result.top_driver.name if result.top_driver else None,
        "components": list(result.breakdown()),
        "evidence": list(result.evidence),
        "assumptions": list(result.assumptions),
        "active_alerts": [f"{a.event} ({a.severity})" for a in alerts],
    }


# ------------------------------------------------------------------ tools --


def list_hubs(deps: Deps, region: str | None = None) -> dict[str, Any]:
    """Every hub in the network, optionally filtered to one region."""
    hubs = deps.registry.hubs
    if region:
        wanted = region.strip().lower()
        hubs = tuple(h for h in hubs if h.region.lower() == wanted)
        if not hubs:
            regions = sorted({h.region for h in deps.registry.hubs})
            return {"error": f"No region named {region!r}. Regions: {', '.join(regions)}."}
    return {
        "count": len(hubs),
        "hazards_supported": list(HAZARDS),
        "hubs": [
            {"hub_id": h.id, "name": h.label, "region": h.region, "county": h.county_name}
            for h in hubs
        ],
    }


def rank_hubs_by_risk(
    deps: Deps,
    hazard: Hazard,
    region: str | None = None,
    hub_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Score and rank hubs for one hazard. THE RANKING IS COMPUTED HERE."""
    if hazard not in HAZARDS:
        return {"error": f"Unsupported hazard {hazard!r}. Supported: {', '.join(HAZARDS)}."}

    hubs = deps.registry.hubs
    if region:
        wanted = region.strip().lower()
        hubs = tuple(h for h in hubs if h.region.lower() == wanted)
        if not hubs:
            return {"error": f"No region named {region!r}."}
    if hub_ids:
        resolved = []
        for hub_id in hub_ids:
            hub = deps.hub_or_error(hub_id)
            if isinstance(hub, str):
                return {"error": hub}
            resolved.append(hub)
        hubs = tuple(resolved)

    results = rank_hubs(hubs, hazard, deps.nri, deps.engine_alerts())
    deps.sources_used.update(
        {"FEMA National Risk Index v1.20.0 (December 2025)", "NWS active alerts"}
    )
    if hazard != "hurricane":
        deps.sources_used.add("Open-Meteo ECMWF IFS reanalysis, 2021-2025")

    payload = {
        "hazard": hazard,
        "hub_count": len(results),
        "scored_by": "deterministic engine (app/scoring/engine.py)",
        "ranking": [
            _assessment_payload(r, i + 1, deps) for i, r in enumerate(results)
        ],
    }
    if deps.alerts_error:
        payload["live_alerts_unavailable"] = deps.alerts_error
    return payload


def explain_hub_risk(deps: Deps, hub_id: str, hazard: Hazard) -> dict[str, Any]:
    """Full component breakdown for one hub and hazard."""
    hub = deps.hub_or_error(hub_id)
    if isinstance(hub, str):
        return {"error": hub}
    if hazard not in HAZARDS:
        return {"error": f"Unsupported hazard {hazard!r}. Supported: {', '.join(HAZARDS)}."}

    result = score_hub(hub, hazard, deps.nri["hubs"][hub.id], deps.engine_alerts().get(hub.id, ()))
    deps.sources_used.update(
        {"FEMA National Risk Index v1.20.0 (December 2025)", "NWS active alerts"}
    )
    if hazard != "hurricane":
        deps.sources_used.add("Open-Meteo ECMWF IFS reanalysis, 2021-2025")

    # The hub's rank within its own region, so "why is Dallas high" can say
    # high RELATIVE TO WHAT without a second tool call.
    peers = tuple(h for h in deps.registry.hubs if h.region == hub.region)
    regional = rank_hubs(peers, hazard, deps.nri, deps.engine_alerts())
    position = next(i for i, r in enumerate(regional) if r.hub_id == hub.id) + 1

    payload = _assessment_payload(result, None, deps)
    payload["rank_within_region"] = f"{position} of {len(regional)} in the {hub.region}"
    payload["grid_cell_provenance"] = climatology.provenance(hub.id)
    return payload


def count_weather_days(
    deps: Deps, hub_id: str, metric: str, year: str | None = None
) -> dict[str, Any]:
    """Count days matching a named metric. This is the direct-measurement path.

    It does NOT go through the risk engine: "what percentage of days had
    snowfall" is a question about the record, not about risk, and routing it
    through a score would answer a question nobody asked.
    """
    hub = deps.hub_or_error(hub_id)
    if isinstance(hub, str):
        return {"error": hub}
    if metric not in MEASUREMENT_METRICS:
        return {
            "error": f"Unknown metric {metric!r}. Available: "
            f"{', '.join(MEASUREMENT_METRICS)}."
        }

    variable, threshold, strict, description = MEASUREMENT_METRICS[metric]
    years = climatology.available_years(hub.id)
    if year and year not in years:
        return {
            "error": f"No data for {year}. This snapshot covers {years[0]}-{years[-1]}.",
            "available_years": list(years),
        }

    count = climatology.count_days(hub.id, variable, threshold, year=year, strict=strict)
    deps.sources_used.add("Open-Meteo ECMWF IFS reanalysis, 2021-2025")

    payload: dict[str, Any] = {
        "hub": hub.label,
        "metric": metric,
        "metric_description": description,
        "period": count.period_label,
        "days_matching": count.days_matching,
        "days_observed": count.days_observed,
        "days_missing": count.days_missing,
        "percent_of_observed_days": round(count.percent_of_observed, 1),
        "days_per_year": round(count.days_per_year, 1),
        "evidence": count.evidence(),
        "grid_cell_provenance": climatology.provenance(hub.id),
    }

    # Snow questions get BOTH counts, unprompted. A user asking "what
    # percentage of days had snowfall" is asking literally and gets the literal
    # answer; giving the operational figure alongside is what lets the agent
    # explain that a trace dusting is not a disruption, without the model
    # having to invent a second threshold.
    if variable == climatology.SNOWFALL:
        other = "disruptive_snowfall" if metric == "any_snowfall" else "any_snowfall"
        o_var, o_threshold, o_strict, o_description = MEASUREMENT_METRICS[other]
        o_count = climatology.count_days(
            hub.id, o_var, o_threshold, year=year, strict=o_strict
        )
        payload["companion_metric"] = {
            "metric": other,
            "metric_description": o_description,
            "days_matching": o_count.days_matching,
            "percent_of_observed_days": round(o_count.percent_of_observed, 1),
        }
        payload["threshold_note"] = (
            "A positive value in this gridded dataset means snow fell somewhere in "
            "the ~9 km cell, not that it accumulated at the hub. Report the literal "
            "figure the user asked for, and mention the operational figure as "
            "context rather than as a correction."
        )
    return payload


def get_hub_alerts(deps: Deps, hub_id: str) -> dict[str, Any]:
    """Active NWS alerts for one hub, right now."""
    hub = deps.hub_or_error(hub_id)
    if isinstance(hub, str):
        return {"error": hub}
    if deps.alerts_error:
        return {"hub": hub.label, "live_alerts_unavailable": deps.alerts_error}

    alerts = deps.alerts.get(hub.id, ())
    deps.sources_used.add("NWS active alerts")
    return {
        "hub": hub.label,
        "fetched_at": nws.fetched_at(),
        "alert_count": len(alerts),
        "alerts": [
            {
                "event": a.event,
                "severity": a.severity,
                "urgency": a.urgency,
                "certainty": a.certainty,
                "headline": a.headline,
                "expires": a.expires,
                "matched_on": a.matched_on,
            }
            for a in alerts
        ],
    }
