"""The deterministic risk engine. No LLM reaches this module, in either direction.

Every number a user is shown originates here or in climatology.py. The agent
may choose WHICH hubs and WHICH hazard to ask about; it never supplies, adjusts
or overrides a score, and the structured contract it fills has no field capable
of carrying one. That is the assignment's "deterministic scoring, not only LLM
output" requirement, enforced by the shape of the types rather than by a
validator that checks after the fact.

THE SCORE

    score = sum(component.value * component.weight) / sum(weights present)

Three components per hazard:

    baseline    FEMA NRI county risk index, 0-100, a national percentile
    historical  how often the hub actually saw a disruptive day, from the
                five-year climatology, scaled against a stated anchor
    current     what the NWS is warning about right now

RENORMALISATION IS THE LOAD-BEARING IDEA

A component can be genuinely absent: FEMA does not model coastal flooding for
Denver, and an alert can carry severity "Unknown". Three ways to handle that,
and two are wrong:

  impute 0   asserts "no risk from this" when the truth is "not measured".
             Silently understates every hub with an unmodelled hazard.
  drop hub   "compare Miami and Houston for flood" returns one hub.
  RENORMALISE over the components that are present, and say so.

The third is what this module does, and the disclosure is not optional: an
absent component always produces an assumption string on the result. The cost
is stated honestly rather than hidden -- a hub scored on two components is not
measured identically to one scored on three, and a reader can see which is
which.

NO ALERTS IS NOT A MISSING COMPONENT. The absence of a warning is real
information, so it scores 0 and is weighted normally. Only an alert whose
severity is unknown is treated as absent. Conflating those two would make
"quiet week" indistinguishable from "we could not tell".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from app.config import ROOT
from app.hubs import Hub
from app.scoring import climatology

Hazard = Literal["winter", "hurricane", "flood"]
HAZARDS: tuple[Hazard, ...] = ("winter", "hurricane", "flood")

WEIGHTS_PATH = ROOT / "app" / "scoring" / "weights.yaml"

_METRIC_VARIABLE = {
    "snow_disruption_days": (climatology.SNOWFALL, "snow_disruption_days_in"),
    "heavy_precip_days": (climatology.PRECIPITATION, "heavy_precip_days_in"),
    "damaging_wind_days": (climatology.WIND_GUST_MAX, "damaging_wind_days_mph"),
}


@lru_cache(maxsize=1)
def load_weights(path: Path | None = None) -> dict[str, Any]:
    return yaml.safe_load((path or WEIGHTS_PATH).read_text(encoding="utf-8"))


# ------------------------------------------------------------- the results --


@dataclass(frozen=True)
class Component:
    """One weighted input to a score.

    `value is None` means NOT MEASURED, and the engine renormalises around it.
    It never means zero.
    """

    name: str
    value: float | None
    weight: float
    source: str
    detail: str

    @property
    def present(self) -> bool:
        return self.value is not None

    @property
    def contribution(self) -> float:
        """This component's share of the final score, in points."""
        return 0.0 if self.value is None else self.value * self.weight


@dataclass(frozen=True)
class HazardScore:
    """A hub's score for one hazard. Constructed only by `score_hub`."""

    hub_id: str
    hub_label: str
    hazard: Hazard
    hazard_label: str
    score: float
    band: str
    components: tuple[Component, ...]
    assumptions: tuple[str, ...]
    evidence: tuple[str, ...]

    @property
    def present_components(self) -> tuple[Component, ...]:
        return tuple(c for c in self.components if c.present)

    @property
    def top_driver(self) -> Component | None:
        """The component contributing the most points. This is what makes
        'which component contributed most?' a lookup rather than a judgement."""
        present = self.present_components
        return max(present, key=lambda c: c.contribution) if present else None

    def breakdown(self) -> tuple[str, ...]:
        """Human-readable arithmetic, for display and for the narrator. Every
        figure traces to a Component, so none of it can be invented."""
        lines = []
        for component in self.components:
            if component.present:
                lines.append(
                    f"{component.name}: {component.value:.1f} x {component.weight:.2f} "
                    f"= {component.contribution:.1f} pts ({component.detail})"
                )
            else:
                lines.append(f"{component.name}: not measured -- {component.detail}")
        return tuple(lines)


# ---------------------------------------------------------------- scoring ---


def band_for(score: float, weights: dict[str, Any] | None = None) -> str:
    for band in (weights or load_weights())["bands"]:
        if score < band["max"]:
            return str(band["label"])
    return str((weights or load_weights())["bands"][-1]["label"])


def _baseline_component(
    hub: Hub, hazard_config: dict[str, Any], nri: dict[str, Any], weight: float
) -> tuple[Component, list[str]]:
    """FEMA NRI exposure, combined across the hazard's NRI dimensions."""
    assumptions: list[str] = []
    names = hazard_config["baseline_hazards"]
    combine = hazard_config["baseline_combine"]

    present: list[tuple[str, float]] = []
    absent: list[str] = []
    for name in names:
        record = nri["hazards"].get(name, {})
        score = record.get("risk_score")
        if score is None:
            absent.append(name)
        else:
            present.append((name, float(score)))

    if absent and present:
        assumptions.append(
            f"FEMA NRI does not model {', '.join(a.replace('_', ' ') for a in absent)} "
            f"for {hub.county_name} County ({hub.nri_fips}); the baseline for "
            f"{hub.label} uses {', '.join(p[0].replace('_', ' ') for p in present)} only."
        )

    if not present:
        return (
            Component(
                name="baseline",
                value=None,
                weight=weight,
                source="FEMA National Risk Index v1.20.0",
                detail=f"NRI models none of {', '.join(names)} for {hub.nri_fips}",
            ),
            assumptions,
        )

    values = [v for _, v in present]
    value = max(values) if combine == "max" else sum(values) / len(values)
    labels = ", ".join(f"{n.replace('_', ' ')}={v:.1f}" for n, v in present)
    return (
        Component(
            name="baseline",
            value=value,
            weight=weight,
            source="FEMA National Risk Index v1.20.0",
            detail=f"{combine} of {labels}",
        ),
        assumptions,
    )


def _historical_component(
    hub: Hub, hazard_config: dict[str, Any], config: dict[str, Any], weight: float
) -> tuple[Component, list[str], list[str]]:
    """Frequency of disruptive days, scaled against a stated anchor."""
    metric = hazard_config["historical_metric"]
    variable, threshold_key = _METRIC_VARIABLE[metric]
    threshold = float(config["climatology_thresholds"][threshold_key])
    anchor = float(hazard_config["anchor_days_per_year"])

    count = climatology.count_days(hub.id, variable, threshold)
    # Linear to the anchor, then clipped. Stated as an assumption because the
    # anchor is a judgement, not a measurement.
    value = min(100.0, count.days_per_year / anchor * 100.0)

    assumptions = [
        f"The historical component maps {anchor:.0f} qualifying days per year to a "
        f"score of 100, linearly and clipped there. That anchor is a prototype "
        f"assumption, not a measured relationship."
    ]
    evidence = [
        f"{hub.label}: {count.days_per_year:.1f} days/year with {variable} "
        f">= {threshold} ({count.evidence()})"
    ]
    return (
        Component(
            name="historical",
            value=value,
            weight=weight,
            source=f"Open-Meteo ECMWF IFS reanalysis, {count.period_label}",
            detail=f"{count.days_per_year:.1f} days/yr vs {anchor:.0f}-day anchor",
        ),
        assumptions,
        evidence,
    )


def _current_component(
    hub: Hub, hazard_config: dict[str, Any], config: dict[str, Any],
    alerts: tuple[dict[str, Any], ...], weight: float,
) -> tuple[Component, list[str], list[str]]:
    """Live NWS alerts for this hub, filtered to this hazard's event types."""
    severity_scores = config["alert_severity_scores"]
    relevant_events = set(hazard_config["alert_events"])

    scored: list[tuple[float, str]] = []
    unscoreable: list[str] = []
    for alert in alerts:
        if alert.get("event") not in relevant_events:
            continue
        severity = alert.get("severity")
        if severity in severity_scores:
            scored.append((float(severity_scores[severity]), str(alert["event"])))
        else:
            unscoreable.append(f"{alert.get('event')} (severity {severity!r})")

    assumptions: list[str] = []
    evidence: list[str] = []

    if scored:
        # MAX, not sum: two simultaneous warnings do not double the disruption,
        # and summing would let a quiet hub with many advisories outrank a hub
        # under a single Extreme warning.
        value, event = max(scored, key=lambda pair: pair[0])
        detail = f"{len(scored)} active alert(s); worst is {event}"
        evidence.extend(
            f"{hub.label}: active NWS alert {name} (severity score {score:.0f})"
            for score, name in sorted(scored, reverse=True)
        )
    elif unscoreable:
        # Every relevant alert had an unusable severity: the component is not
        # measured, rather than zero.
        value = None
        detail = f"{len(unscoreable)} active alert(s) with unknown severity"
    else:
        # Genuinely quiet. This IS a measurement.
        value = 0.0
        detail = "no active NWS alerts for this hazard"
        evidence.append(f"{hub.label}: no active NWS alerts for this hazard")

    if unscoreable:
        assumptions.append(
            f"{hub.label} has active alert(s) whose severity the NWS reported as "
            f"unknown ({'; '.join(unscoreable)}). They are excluded from the current "
            f"component rather than scored as zero."
        )

    return (
        Component(
            name="current",
            value=value,
            weight=weight,
            source="NWS active alerts",
            detail=detail,
        ),
        assumptions,
        evidence,
    )


def score_hub(
    hub: Hub,
    hazard: Hazard,
    nri: dict[str, Any],
    alerts: tuple[dict[str, Any], ...] = (),
    weights: dict[str, Any] | None = None,
) -> HazardScore:
    """Score one hub for one hazard. Pure: everything it reads is an argument
    or the frozen climatology."""
    config = weights or load_weights()
    if hazard not in config["hazards"]:
        raise ValueError(f"unknown hazard {hazard!r}; expected one of {HAZARDS}")
    hazard_config = config["hazards"][hazard]
    component_weights = hazard_config["weights"]

    assumptions: list[str] = []
    evidence: list[str] = []

    baseline, baseline_assumptions = _baseline_component(
        hub, hazard_config, nri, float(component_weights["baseline"])
    )
    assumptions += baseline_assumptions
    if baseline.present:
        evidence.append(
            f"{hub.label}: FEMA NRI {hazard} baseline {baseline.value:.1f}/100 "
            f"({baseline.detail})"
        )

    # A hazard may declare no historical component at all. That is NOT the
    # same as a component that could not be measured: it is excluded from the
    # model by design, so it produces a methodology note rather than a
    # "could not be measured" assumption, and it is not part of the
    # renormalisation. Conflating the two would tell every reader that
    # hurricane data was missing, when in fact it was judged unfit.
    historical: Component | None = None
    if hazard_config.get("historical_metric"):
        historical, historical_assumptions, historical_evidence = _historical_component(
            hub, hazard_config, config, float(component_weights["historical"])
        )
        assumptions += historical_assumptions
        evidence += historical_evidence
    elif hazard_config.get("methodology_note"):
        assumptions.append(str(hazard_config["methodology_note"]).strip())

    current, current_assumptions, current_evidence = _current_component(
        hub, hazard_config, config, alerts, float(component_weights["current"])
    )
    assumptions += current_assumptions
    evidence += current_evidence

    components = tuple(c for c in (baseline, historical, current) if c is not None)
    present = [c for c in components if c.present]

    if not present:
        raise ValueError(
            f"no component could be measured for {hub.id}/{hazard} -- refusing to "
            "emit a score with no inputs"
        )

    # The renormalisation. Dividing by the weights PRESENT, not the weights
    # declared, is what keeps an absent component from dragging the score down.
    weight_present = sum(c.weight for c in present)
    score = sum(c.contribution for c in present) / weight_present

    if len(present) < len(components):
        missing = ", ".join(c.name for c in components if not c.present)
        assumptions.append(
            f"The {missing} component could not be measured for {hub.label}, so the "
            f"remaining weights were renormalised over {weight_present:.2f}. Its score "
            f"is therefore not built from the same inputs as a fully-measured hub."
        )

    if hub.keys_diverge:
        assumptions.append(
            f"{hub.label}: FEMA and the NWS use different county geographies here "
            f"(NRI {hub.nri_fips}, NWS {hub.nws_county_fips}), so the baseline and the "
            f"live alerts were joined on different boundaries."
        )

    # Round BEFORE banding, so the band always agrees with the score the user
    # is shown. Banding the raw float let 59.99999 and 60.00001 both display
    # as "60.0" while landing in different bands -- two hubs showing the same
    # number with different labels, which reads as a broken system.
    rounded = round(score, 1)

    return HazardScore(
        hub_id=hub.id,
        hub_label=hub.label,
        hazard=hazard,
        hazard_label=hazard_config["label"],
        score=rounded,
        band=band_for(rounded, config),
        components=components,
        assumptions=tuple(assumptions),
        evidence=tuple(evidence),
    )


def rank_hubs(
    hubs: tuple[Hub, ...],
    hazard: Hazard,
    nri_snapshot: dict[str, Any],
    alerts_by_hub: dict[str, tuple[dict[str, Any], ...]] | None = None,
    weights: dict[str, Any] | None = None,
) -> tuple[HazardScore, ...]:
    """Score every hub and order them, highest risk first.

    THIS is the function that answers "which hubs are most exposed". The agent
    is never asked to rank; it is handed this ordering and explains it. Ties
    break on hub id so the order is stable across runs -- a ranking that
    reshuffles equal scores between calls looks like a bug to a user comparing
    two answers.
    """
    alerts_by_hub = alerts_by_hub or {}
    scores = [
        score_hub(
            hub,
            hazard,
            nri_snapshot["hubs"][hub.id],
            alerts_by_hub.get(hub.id, ()),
            weights,
        )
        for hub in hubs
    ]
    return tuple(sorted(scores, key=lambda s: (-s.score, s.hub_id)))
