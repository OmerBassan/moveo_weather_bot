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

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml

from app.config import ROOT, load_config
from app.hubs import Hub, load_hubs
from app.scoring import climatology

Hazard = Literal["winter", "hurricane", "flood"]
HAZARDS: tuple[Hazard, ...] = ("winter", "hurricane", "flood")

# WHICH COMPONENTS DESCRIBE A HUB'S STANDING EXPOSURE, and which describe what
# is happening this week. Named here rather than spelled as string literals at
# each use, because the split carries a decision: a resilience upgrade acts on
# the first group and cannot act on the second.
STRUCTURAL_COMPONENTS: tuple[str, ...] = ("baseline", "historical")
TRANSIENT_COMPONENT = "current"

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


def _renormalise(components: Iterable[Component]) -> float | None:
    """Weighted mean over the components that were MEASURED. The one place in
    this system where a set of components becomes a score.

    Extracted so that the total score and the structural score are computed by
    identical arithmetic rather than by two expressions that agree until one is
    edited. Returns None when nothing was measured -- refusing to produce a
    number is the correct answer there, and the callers differ on what to do
    about it: `score_hub` raises, `structural_score` reports None.
    """
    present = [c for c in components if c.present]
    weight_present = sum(c.weight for c in present)
    if not present or weight_present == 0:
        return None
    return sum(c.contribution for c in present) / weight_present


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
        figure traces to a Component, so none of it can be invented.

        THE SHARE OF SCORE IS STATED, NOT LEFT TO BE DERIVED. Without it, a
        model asked "which component contributed most?" divides the
        contribution by the total itself -- observed: Haiku 4.5 answered "61%
        of the score" for 44.8/73.1, arithmetic it performed on user-visible
        numbers. The division was correct, but a correct guess and a wrong one
        are indistinguishable to a reader. Supplying the percentage removes
        the need, the same move as supplying the ranking gaps.
        """
        lines = []
        for component in self.components:
            if component.present:
                share = (
                    component.contribution / self.score * 100.0 if self.score else 0.0
                )
                lines.append(
                    f"{component.name}: {component.value:.1f} x {component.weight:.2f} "
                    f"= {component.contribution:.1f} pts, {share:.0f}% of the "
                    f"{self.score:.1f} score ({component.detail})"
                )
            else:
                lines.append(f"{component.name}: not measured -- {component.detail}")
        return tuple(lines)


    # ------------------------------------------- the investment reading --
    #
    # The score above answers "how exposed is this hub right now". These answer
    # "is this hub's exposure the kind a resilience upgrade addresses", which is
    # a different question and the one the annual budget turns on.
    #
    # They are PROPERTIES, not fields: the dataclass has one construction site
    # and adding fields there would ripple into every consumer that mirrors its
    # shape. Nothing here can change `score`.

    @property
    def structural_score(self) -> float | None:
        """The score rebuilt from standing exposure alone, excluding `current`.

        WHY THIS IS None AND NEVER A NUMBER WHEN NOTHING STRUCTURAL WAS
        MEASURED. Two tempting fallbacks, both wrong for the same reason the
        module refuses to impute zero for an absent component:

          0.0          asserts "no structural exposure" where the truth is
                       "FEMA does not model hurricanes for this county".
          the total    lets the transient component BECOME the structural one,
                       which is the precise inversion this reading exists to
                       expose.

        It happens for eight hubs on hurricane, so the choice is not
        hypothetical -- it is a fifth of that column.
        """
        value = _renormalise(
            c for c in self.components if c.name in STRUCTURAL_COMPONENTS
        )
        return None if value is None else round(value, 1)

    @property
    def transient_score(self) -> float | None:
        """The `current` component's own value: alerts and near-term forecast.

        None when the component was not measurable (every relevant alert came
        back with unknown severity), which is not the same as the 0.0 that a
        genuinely quiet week scores.
        """
        for component in self.components:
            if component.name == TRANSIENT_COMPONENT:
                return None if component.value is None else round(component.value, 1)
        return None

    @property
    def structural_band(self) -> str | None:
        score = self.structural_score
        return None if score is None else band_for(score)

    @property
    def transient_band(self) -> str | None:
        score = self.transient_score
        return None if score is None else band_for(score)

    @property
    def investability(self) -> str | None:
        """Which tier this hub-hazard falls in for investment purposes.

        Standing within the whole network, not within whatever hubs the caller
        asked about -- see `structural_cutoff`. None means not tierable.
        """
        return investability_for(
            self.structural_score, self.transient_band, self.hazard
        )


# ---------------------------------------------------------------- scoring ---


def band_for(score: float, weights: dict[str, Any] | None = None) -> str:
    for band in (weights or load_weights())["bands"]:
        if score < band["max"]:
            return str(band["label"])
    return str((weights or load_weights())["bands"][-1]["label"])


@lru_cache(maxsize=None)
def structural_cutoff(hazard: Hazard) -> float | None:
    """The structural score at this network's top-third boundary for a hazard.

    WHY THIS IS A NETWORK CONSTANT AND NOT A PROPERTY OF THE CALLER'S SUBSET.
    A structural score reads only frozen data -- the FEMA snapshot and the
    five-year climatology -- and never an alert or a forecast. So the boundary
    is a fact about the record, computable once and cached, and a hub's tier
    does not depend on which hubs a question happened to mention. Tiering
    against the queried subset would mean "compare Miami and Houston" made both
    of them top-half by arithmetic, and the same hub would carry a different
    tier in the next sentence.

    Returns None when no hub in the network has a measurable structural score
    for the hazard, which cannot happen with the current snapshots but is not
    the caller's problem to assume.

    This reads two files, so `score_hub`'s purity claim now covers the frozen
    NRI snapshot as well as the frozen climatology. Both are committed inputs,
    cached on first read, and identical across a process lifetime.
    """
    config = load_weights()
    one_in = int(config["investability"]["invest_top_one_in"])
    nri = json.loads(
        load_config().nri_snapshot_path.read_text(encoding="utf-8")
    )

    values: list[float] = []
    for hub in load_hubs().hubs:
        hazard_config = config["hazards"][hazard]
        baseline, _ = _baseline_component(
            hub, hazard_config, nri["hubs"][hub.id], float(hazard_config["weights"]["baseline"])
        )
        parts = [baseline]
        if hazard_config.get("historical_metric"):
            historical, _, _ = _historical_component(
                hub, hazard_config, config, float(hazard_config["weights"]["historical"])
            )
            parts.append(historical)
        structural = _renormalise(parts)
        if structural is not None:
            values.append(round(structural, 1))

    if not values:
        return None
    values.sort(reverse=True)
    # Floor division: 40 hubs -> the top 13, 32 -> the top 10. The boundary is
    # the LAST member's score, and the comparison in `investability_for` is
    # inclusive, so hubs tied with it join the group rather than being cut by
    # an arbitrary tiebreak.
    count = max(1, len(values) // one_in)
    return values[count - 1]


@lru_cache(maxsize=1)
def _alert_index(weights_key: int = 0) -> dict[str, tuple[Hazard, ...]]:
    """NWS event name -> the hazards whose score it can move."""
    config = load_weights()
    index: dict[str, list[Hazard]] = {}
    for hazard, hazard_config in config["hazards"].items():
        for event in hazard_config["alert_events"]:
            index.setdefault(str(event), []).append(hazard)  # type: ignore[arg-type]
    return {event: tuple(hazards) for event, hazards in index.items()}


def hazards_scored_by(event: str) -> tuple[Hazard, ...]:
    """Which hazards, if any, this alert contributes to.

    EMPTY IS THE INTERESTING ANSWER, and the reason this exists. The NWS issues
    alerts for far more than the three hazards modelled here -- Dense Fog
    Advisory, Air Quality Alert, Special Weather Statement -- and the alert feed
    is fetched per HUB, not per hazard. So a hub's active alerts routinely
    include events that contribute exactly 0.0 to the score being discussed.

    Observed: asked to compare two hubs on current conditions after a WINTER
    ranking, the agent answered with a Dense Fog Advisory. Fog is genuinely
    dangerous for road freight and the answer was factually true, but it read as
    though fog were driving the winter score, which it cannot -- it appears in
    no hazard's `alert_events`. Nothing in the payload said so.

    Callers use this to LABEL an alert rather than to filter it. Dropping the
    unscored ones would be worse: an operator wants to know a fog advisory is
    live, and hiding it to keep the score tidy is the opposite of the disclosure
    discipline everything else here follows. The fix is to say which number it
    moves, which is none.
    """
    return _alert_index().get(event, ())


def investability_for(
    structural_score: float | None,
    transient_band: str | None,
    hazard: Hazard,
    weights: dict[str, Any] | None = None,
) -> str | None:
    """Which investment tier a hub-hazard falls in.

    Invest is standing among this network's most exposed for the hazard; Watch
    is a hub outside that group with something happening now; Low is neither.
    The reasoning for a relative boundary, and the measurements that forced it,
    are in weights.yaml.

    None means NOT TIERABLE, for one of two reasons:

      structural_score is None    nothing structural was measured, so there is
                                  no persistent exposure to invest against.
                                  Eight hubs on hurricane.
      transient not measured      only matters outside the Invest group, where
      outside the Invest group    the tier turns on the live reading. Returning
                                  "Low" there would answer a test that was
                                  never run -- the same mistake as scoring an
                                  unknown-severity alert as zero.

    A structurally Low hub with a hurricane bearing down is "Watch", not
    "Invest", and that looks wrong until you recall what the tier is for:
    capital is allocated against exposure that persists, not against a storm
    that will pass. Callers ranking on this MUST say so; `rank_portfolio` does.
    """
    config = (weights or load_weights())["investability"]
    labels = config["labels"]

    if structural_score is None:
        return None

    cutoff = structural_cutoff(hazard)
    if cutoff is not None and structural_score >= cutoff:
        return str(labels["invest"])

    if transient_band is None:
        return None
    if transient_band in config["watch_transient_bands"]:
        return str(labels["watch"])
    return str(labels["low"])


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
        f"{count.comparison} {threshold} ({count.evidence()})"
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
    forecast: dict[str, Any] | None = None,
) -> tuple[Component, list[str], list[str]]:
    """Live near-term conditions: issued alerts AND the quantitative forecast.

    Two readings of the same thing, so the component takes the HIGHER. The
    alert is authoritative -- a forecaster decided it warranted one -- and the
    forecast is the earlier signal. Before the forecast was added, a hub with
    six inches of snow arriving and no warning yet issued scored zero here,
    which read as calm.
    """
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

    # ---- the forecast reading -----------------------------------------
    forecast_value: float | None = None
    forecast_detail = ""
    metric = hazard_config.get("forecast_metric")
    if forecast is not None and metric:
        anchor = float(hazard_config["forecast_anchor"])
        floor = float(hazard_config.get("forecast_floor", 0.0))
        measured = float(forecast.get(metric) or 0.0)
        span = anchor - floor
        forecast_value = (
            0.0
            if measured <= floor or span <= 0
            else min(100.0, (measured - floor) / span * 100.0)
        )
        units = "in" if metric != "max_wind_gust_mph" else "mph"
        forecast_detail = (
            f"{measured:.2f} {units} forecast over {forecast.get('window_hours', 72)}h "
            f"(scores from {floor:g} to {anchor:g} {units})"
        )
        evidence.append(
            f"{hub.label}: NWS forecast {metric.replace('_', ' ')} {measured:.2f} {units} "
            f"in the next {forecast.get('window_hours', 72)} hours"
        )
        assumptions.append(
            f"The forecast component scores 0 below {floor:g} {units} over "
            f"{forecast.get('window_hours', 72)} hours and reaches 100 at "
            f"{anchor:g} {units}. Both bounds are prototype assumptions."
        )

    # ---- the alert reading ---------------------------------------------
    alert_value: float | None = None
    alert_detail = "no active NWS alerts for this hazard"
    if scored:
        # MAX, not sum: two simultaneous warnings do not double the disruption,
        # and summing would let a quiet hub with many advisories outrank a hub
        # under a single Extreme warning.
        alert_value, event = max(scored, key=lambda pair: pair[0])
        alert_detail = f"{len(scored)} active alert(s); worst is {event}"
        evidence.extend(
            f"{hub.label}: active NWS alert {name} (severity score {score:.0f})"
            for score, name in sorted(scored, reverse=True)
        )
    elif unscoreable:
        # Every relevant alert had an unusable severity: not measured.
        alert_value = None
        alert_detail = f"{len(unscoreable)} active alert(s) with unknown severity"
    else:
        alert_value = 0.0
        evidence.append(f"{hub.label}: no active NWS alerts for this hazard")

    # ---- combine --------------------------------------------------------
    readings = [v for v in (alert_value, forecast_value) if v is not None]
    if not readings:
        value = None
        detail = alert_detail
    else:
        value = max(readings)
        # `alert_value or -1.0` would be WRONG here: a legitimate alert score
        # of 0.0 ("no active alerts") is falsy, so it would become -1.0 and
        # every quiet hub would be reported as "forecast-driven" even when the
        # forecast is also 0.0. Compare against None explicitly.
        alert_baseline = -1.0 if alert_value is None else alert_value
        if forecast_value is not None and forecast_value > alert_baseline:
            detail = f"forecast-driven: {forecast_detail}"
        elif forecast_detail:
            detail = f"{alert_detail}; forecast {forecast_detail}"
        else:
            detail = alert_detail

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
    forecast: dict[str, Any] | None = None,
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
        hub, hazard_config, config, alerts, float(component_weights["current"]), forecast
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
    score = _renormalise(components)
    if score is None:  # unreachable: `present` is non-empty, checked above
        raise ValueError(f"renormalisation produced no score for {hub.id}/{hazard}")

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
    forecasts_by_hub: dict[str, dict[str, Any]] | None = None,
) -> tuple[HazardScore, ...]:
    """Score every hub and order them, highest risk first.

    THIS is the function that answers "which hubs are most exposed". The agent
    is never asked to rank; it is handed this ordering and explains it. Ties
    break on hub id so the order is stable across runs -- a ranking that
    reshuffles equal scores between calls looks like a bug to a user comparing
    two answers.
    """
    alerts_by_hub = alerts_by_hub or {}
    forecasts_by_hub = forecasts_by_hub or {}
    scores = [
        score_hub(
            hub,
            hazard,
            nri_snapshot["hubs"][hub.id],
            alerts_by_hub.get(hub.id, ()),
            weights,
            forecasts_by_hub.get(hub.id),
        )
        for hub in hubs
    ]
    return tuple(sorted(scores, key=lambda s: (-s.score, s.hub_id)))


# ------------------------------------------------------------- portfolio ---
#
# "Which handful of hubs should we invest in this year?" is the assignment's
# headline question and it is inherently CROSS-HAZARD. `rank_hubs` takes one
# hazard, so answering it previously meant three rankings merged by hand -- or,
# worse, merged by the model, which is the one thing this module exists to
# prevent.


THE_TIER_IS_NOT_TODAYS_RISK = (
    "Hubs are grouped by STRUCTURAL exposure -- the FEMA baseline and the "
    "historical record -- not by today's risk score. A hub can carry a high "
    "current score and still sit outside the Invest group, because a resilience "
    "upgrade acts on exposure that persists and cannot act on a storm that will "
    "pass. Where the two readings disagree, the disagreement is the point."
)

CROSS_HAZARD_COMPARABILITY = (
    "Scores are NOT compared across hazards. Hurricane is scored from two "
    "components and winter and flood from three, so their totals are not one "
    "measurement. Grouping is by tier, which is ordinal and means the same "
    "thing for every hazard; within a tier hubs are ordered by how many hazards "
    "put them there and then by how far each sits above its own hazard's "
    "boundary, so no hazard's scores are weighed against another's."
)

THE_TIER_IS_RELATIVE = (
    "Invest means standing in this network's most exposed third for a hazard, "
    "not an absolute level of risk. FEMA's National Risk Index is loss-weighted "
    "and ranked against every US county, so a network of major metropolitan "
    "hubs sits high on it by construction; a within-network ranking is the "
    "comparison an investment choice among these hubs actually needs. Adding or "
    "removing hubs can move the boundary."
)


@dataclass(frozen=True)
class PortfolioEntry:
    """One hub's standing across every hazard, for an investment shortlist."""

    hub_id: str
    hub_label: str
    region: str
    investability: str | None
    # Every hazard that reached this hub's best tier -- usually one, sometimes
    # two. NAMING ALL OF THEM RATHER THAN PICKING A WINNER: when Miami reaches
    # Invest on both hurricane and flood, choosing one and hiding the other is a
    # judgement the data does not support, and "worst hazard by score" would be
    # exactly the cross-hazard score comparison this module refuses to make.
    driving_hazards: tuple[Hazard, ...]
    # Which of the driving hazards represents the hub in a one-line summary:
    # the most structurally exposed of them. Stored rather than re-derived so a
    # caller quoting `score` and a caller quoting a component breakdown cannot
    # end up describing two different hazards.
    lead_hazard: Hazard
    structural_score: float | None
    # How far the hub's structural score sits above its HAZARD'S OWN network
    # boundary, as a ratio. 1.00 is exactly at the cutoff.
    #
    # THIS IS WHAT MAKES THE CROSS-HAZARD ORDER LEGITIMATE. Ordering the Invest
    # group by raw structural score compares distributions rather than hubs:
    # winter's cutoff is 73.7 and flood's is 87.9, so every winter hub sorted
    # BELOW every flood hub, and Salt Lake City -- the most exposed winter hub
    # in the network -- landed ninth behind single-hazard flood hubs. Measured,
    # not theorised. Dividing by each hazard's own cutoff compares each hub
    # against the distribution it actually belongs to.
    exceedance: float | None
    score: float
    band: str
    by_hazard: tuple[HazardScore, ...]
    assumptions: tuple[str, ...]

    @property
    def lead(self) -> HazardScore:
        """The full result for `lead_hazard`, for callers that need its
        component breakdown or evidence."""
        return next(r for r in self.by_hazard if r.hazard == self.lead_hazard)


def _tier_rank(tier: str | None, weights: dict[str, Any] | None = None) -> int:
    """Order the tiers. Not-tierable sorts last: a hub with no measurable
    structural exposure is not a candidate, but it is not hidden either."""
    labels = (weights or load_weights())["investability"]["labels"]
    order = {labels["invest"]: 0, labels["watch"]: 1, labels["low"]: 2}
    return order.get(tier or "", 3)


def rank_portfolio(
    hubs: tuple[Hub, ...],
    nri_snapshot: dict[str, Any],
    alerts_by_hub: dict[str, tuple[dict[str, Any], ...]] | None = None,
    weights: dict[str, Any] | None = None,
    forecasts_by_hub: dict[str, dict[str, Any]] | None = None,
    hazards: tuple[Hazard, ...] = HAZARDS,
) -> tuple[PortfolioEntry, ...]:
    """Rank every hub across every hazard for investment priority.

    ORDERED BY TIER FIRST, AND THAT IS THE WHOLE DESIGN. Sorting by score would
    require comparing a two-component hurricane score against a three-component
    winter one, which `describe_methodology` explicitly disclaims. The tier is
    ordinal and hazard-independent, so it can carry the cross-hazard ordering
    that the scores cannot.

    WITHIN A TIER, TWO HAZARD-NEUTRAL CRITERIA, in this order:

      how many hazards put the hub in this tier   A hub in the Invest group on
                                                  three hazards has a broader
                                                  case than one qualifying on a
                                                  single hazard. A COUNT, so no
                                                  magnitudes are compared.
      exceedance over the hazard's own cutoff     Normalises for the fact that
                                                  each hazard's network
                                                  distribution sits at a
                                                  different level. See
                                                  `PortfolioEntry.exceedance`.

    Ties break on hub id, matching `rank_hubs`, so the ordering is stable across
    runs.
    """
    config = weights or load_weights()
    scored: list[PortfolioEntry] = []

    for hub in hubs:
        by_hazard = tuple(
            score_hub(
                hub,
                hazard,
                nri_snapshot["hubs"][hub.id],
                (alerts_by_hub or {}).get(hub.id, ()),
                config,
                (forecasts_by_hub or {}).get(hub.id),
            )
            for hazard in hazards
        )

        best_rank = min(_tier_rank(r.investability, config) for r in by_hazard)
        driving = tuple(
            r for r in by_hazard if _tier_rank(r.investability, config) == best_rank
        )
        # Among the hazards sharing the best tier, the one with the highest
        # structural exposure represents the hub. A None structural score sorts
        # last so a measurable hazard always speaks for the hub where one
        # exists.
        lead = max(
            driving,
            key=lambda r: (
                r.structural_score is not None,
                r.structural_score or 0.0,
                r.hazard,
            ),
        )

        cutoffs = [
            (r.structural_score, structural_cutoff(r.hazard))
            for r in driving
            if r.structural_score is not None
        ]
        exceedance = max(
            (score / cutoff for score, cutoff in cutoffs if cutoff),
            default=None,
        )

        assumptions = [THE_TIER_IS_NOT_TODAYS_RISK, THE_TIER_IS_RELATIVE]
        if len(hazards) > 1:
            assumptions.append(CROSS_HAZARD_COMPARABILITY)
        assumptions.extend(a for r in driving for a in r.assumptions)

        scored.append(
            PortfolioEntry(
                hub_id=hub.id,
                hub_label=hub.label,
                region=hub.region,
                investability=lead.investability,
                driving_hazards=tuple(r.hazard for r in driving),
                lead_hazard=lead.hazard,
                structural_score=lead.structural_score,
                exceedance=None if exceedance is None else round(exceedance, 3),
                score=lead.score,
                band=lead.band,
                by_hazard=by_hazard,
                assumptions=tuple(dict.fromkeys(assumptions)),
            )
        )

    return tuple(
        sorted(
            scored,
            key=lambda e: (
                _tier_rank(e.investability, config),
                -len(e.driving_hazards),
                -(e.exceedance or 0.0),
                e.hub_id,
            ),
        )
    )
