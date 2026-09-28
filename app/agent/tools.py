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
    load_config,
)
from app import alerting
from app.hubs import Hub, HubRegistry, load_hubs
from app.scoring import climatology
from app.scoring.engine import (
    CROSS_HAZARD_COMPARABILITY,
    HAZARDS,
    hazards_scored_by,
    THE_TIER_IS_NOT_TODAYS_RISK,
    THE_TIER_IS_RELATIVE,
    Hazard,
    HazardScore,
    load_weights,
    rank_hubs,
    rank_portfolio,
    score_hub,
    structural_cutoff,
)
from app.tools import nws

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class Metric:
    """One named measurement `count_weather_days` accepts.

    Named metrics rather than a free variable+threshold pair, so the model
    cannot invent a threshold and present the result as a measurement.

    `direction` is a field rather than an assumption because a threshold alone
    does not say which side of it counts: 1 inch of snow is bad when exceeded,
    32F is bad when undercut. It is spelled out per metric so that the operator
    applied and the `description` shown to the model are written side by side
    and can be read against each other.
    """

    variable: str
    threshold: float
    strict: bool
    direction: str
    description: str
    units: str
    # The metric that answers the OTHER reading of the same question. A literal
    # count ("did it rain at all") and an operational one ("did it rain enough
    # to matter") are both correct and differ by a lot, so whichever is asked
    # for, the other is returned as context. Declared per metric rather than
    # hardcoded for snowfall: the asymmetry was the bug -- snow had both
    # readings and rain only had the operational one, so "how many days did it
    # rain" was silently answered at a one-inch storm threshold.
    companion: str | None = None
    # True when the threshold is an operational judgement rather than a
    # physical boundary, which is a caveat the answer has to carry. 32F is
    # where water freezes; 1 inch of snow is somebody's opinion about roads.
    threshold_is_judgement: bool = False
    # The key in weights.yaml's `climatology_thresholds` that the SCORING
    # engine uses for this same concept, where one exists.
    #
    # TWO SOURCES FOR ONE THRESHOLD IS A LATENT DIVERGENCE. `disruptive_snowfall`
    # reads SNOW_DAY_DISRUPTION_THRESHOLD_IN from app/config.py, which is
    # env-overridable; the engine's historical component reads
    # `snow_disruption_days_in` from weights.yaml. They are both 1.0 today and
    # agree only by coincidence -- setting WRA_SNOW_DAY_THRESHOLD_IN=2.0 moves
    # the measurement and leaves the score alone.
    #
    # That is tolerable for a literal measurement, where the user's question
    # sets the threshold. It is not tolerable for `year_by_year`, whose whole
    # purpose is to describe the exposure that SIZES a resilience investment:
    # a "worst year" counted at a threshold the score does not use is a worst
    # year for nothing. So that tool resolves the threshold through this key
    # and reports which source it used.
    engine_threshold_key: str | None = None


MEASUREMENT_METRICS: dict[str, Metric] = {
    "any_snowfall": Metric(
        climatology.SNOWFALL, SNOW_DAY_LITERAL_THRESHOLD_IN, True, climatology.ABOVE,
        "days with any recorded snowfall at all", "in",
        companion="disruptive_snowfall",
    ),
    "disruptive_snowfall": Metric(
        climatology.SNOWFALL, SNOW_DAY_DISRUPTION_THRESHOLD_IN, False, climatology.ABOVE,
        f"days with at least {SNOW_DAY_DISRUPTION_THRESHOLD_IN} inch of fresh snow", "in",
        companion="any_snowfall", threshold_is_judgement=True,
        engine_threshold_key="snow_disruption_days_in",
    ),
    "any_precipitation": Metric(
        climatology.PRECIPITATION, 0.0, True, climatology.ABOVE,
        "days with any recorded precipitation at all", "in",
        companion="heavy_precipitation",
    ),
    "heavy_precipitation": Metric(
        climatology.PRECIPITATION, 1.0, False, climatology.ABOVE,
        "days with at least 1 inch of precipitation", "in",
        companion="any_precipitation", threshold_is_judgement=True,
        engine_threshold_key="heavy_precip_days_in",
    ),
    "damaging_wind": Metric(
        climatology.WIND_GUST_MAX, 58.0, False, climatology.ABOVE,
        "days with wind gusts at or above 58 mph", "mph",
        threshold_is_judgement=True, engine_threshold_key="damaging_wind_days_mph",
    ),
    "freezing": Metric(
        climatology.TEMP_MIN, 32.0, False, climatology.BELOW,
        "days whose minimum temperature reached 32F or below", "F",
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
    # Quantitative NWS forecasts, fetched lazily for the hubs actually being
    # scored -- 40 gridpoint calls on every turn would put the whole network
    # on the critical path of a question about two hubs.
    forecasts: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Names of the tools this turn called, in order. Which tools the agent
    # chose is the single most useful thing to know when an answer looks
    # wrong, and nothing else records it.
    tools_called: list[str] = field(default_factory=list)
    sources_used: set[str] = field(default_factory=set)
    # Caveats raised by the MEASUREMENT path, collected here so `assemble` can
    # attach them the way it already attaches the engine's. Without this the
    # disclosure was structural on the scoring path and prompt-dependent on the
    # counting path -- the weaker of the two, and the one whose numbers a
    # reader is least able to sanity-check.
    measurement_assumptions: list[str] = field(default_factory=list)

    def engine_alerts(self) -> dict[str, tuple[dict[str, Any], ...]]:
        return nws.alerts_as_engine_input(self.alerts)

    def ensure_forecasts(self, hubs: tuple[Hub, ...]) -> dict[str, dict[str, Any]]:
        """Fetch forecasts for any of `hubs` not already held, concurrently.

        A failure for one hub leaves it absent rather than zero: the scoring
        engine renormalises around a missing reading, and a zeroed forecast
        would read as "calm" for a hub we simply could not reach.
        """
        missing = tuple(h for h in hubs if h.id not in self.forecasts)
        if missing:
            for hub_id, forecast in nws.fetch_forecasts(missing).items():
                self.forecasts[hub_id] = forecast.as_dict()
            self.sources_used.add("NWS quantitative gridpoint forecast")
        return self.forecasts

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


# How many hubs in a ranking come back with their full component breakdown.
# Beyond this the row is compact. Measured: a 40-hub ranking with full detail
# on every row costs ~9,400 tokens and is the single largest thing this agent
# reads; ~5,300 of that is detail for hubs nobody asked about. A ranking is
# read top-down, and any specific hub further down can be fetched with
# explain_hub_risk for ~365 tokens.
DETAIL_ROWS = 5


def _compact_row(result: HazardScore, rank: int | None, deps: Deps) -> dict[str, Any]:
    """Enough to state and order the result. ~15 tokens."""
    row: dict[str, Any] = {
        "hub_id": result.hub_id,
        "hub": result.hub_label,
        "risk_score": result.score,
        "risk_band": result.band,
    }
    if rank is not None:
        row["rank"] = rank
    if driver := result.top_driver:
        row["top_driver"] = driver.name
    if alerts := deps.alerts.get(result.hub_id, ()):
        row["active_alerts"] = [f"{a.event} ({a.severity})" for a in alerts]
    return row


def _assessment_payload(result: HazardScore, rank: int | None, deps: Deps) -> dict[str, Any]:
    """A full row: adds the arithmetic and the evidence lines. ~145 tokens.

    `assumptions` is deliberately NOT here. They are hub-independent almost
    always -- a 40-hub ranking produced 41 assumption strings of which 2 were
    unique, costing 1,802 tokens to say 86 tokens' worth -- so the caller
    collects them once at the top level.
    """
    payload = _compact_row(result, rank, deps)
    payload["hazard"] = result.hazard
    payload["components"] = list(result.breakdown())
    payload["evidence"] = list(result.evidence)
    return payload


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
    # Grouped by region and emitted as "id=label" pairs rather than one object
    # per hub: the same information for roughly a third of the tokens, and this
    # tool exists only to resolve a name to an id.
    grouped: dict[str, list[str]] = {}
    for h in hubs:
        grouped.setdefault(h.region, []).append(f"{h.id}={h.label}")
    return {
        "count": len(hubs),
        "hazards_supported": list(HAZARDS),
        "hubs_by_region": {r: sorted(v) for r, v in sorted(grouped.items())},
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

    results = rank_hubs(
        hubs, hazard, deps.nri, deps.engine_alerts(),
        forecasts_by_hub=deps.ensure_forecasts(hubs),
    )
    deps.sources_used.update(
        {"FEMA National Risk Index v1.20.0 (December 2025)", "NWS active alerts"}
    )
    if hazard != "hurricane":
        deps.sources_used.add(climatology.historical_source())

    ranking: list[dict[str, Any]] = []
    leader = results[0].score if results else 0.0
    for position, result in enumerate(results, start=1):
        if position <= DETAIL_ROWS:
            row = _assessment_payload(result, position, deps)
        else:
            row = _compact_row(result, position, deps)

        # THE DELTAS ARE COMPUTED HERE SO THE MODEL NEVER SUBTRACTS.
        # Without them, an answer like "Miami is 1.9 points ahead of Houston"
        # is arithmetic the model performed on user-visible numbers -- which
        # contradicts the whole point of keeping scores out of its schema.
        # Supplying the difference removes the opportunity rather than
        # policing it, the same move as omitting risk_score from AgentDraft.
        row["gap_to_leader"] = round(leader - result.score, 1)
        if position < len(results):
            row["gap_to_next"] = round(result.score - results[position].score, 1)
            # A TIE IS FLAGGED, NOT LEFT IN THE ARITHMETIC. Scores round to one
            # decimal before banding and ties break alphabetically on hub id,
            # so "the top three" can mean funding three of four equal hubs in
            # alphabetical order. gap_to_next of 0.0 already said so, but only
            # to a reader who thought to subtract; naming it is what gets it
            # into the answer when someone asks for a cutoff.
            if row["gap_to_next"] == 0.0:
                row["tied_with_next"] = True
        ranking.append(row)

    # Collected once, deduplicated, order preserved.
    assumptions = list(dict.fromkeys(a for r in results for a in r.assumptions))

    payload: dict[str, Any] = {
        "hazard": hazard,
        "hub_count": len(results),
        "scored_by": "deterministic engine (app/scoring/engine.py)",
        "ranking": ranking,
        "assumptions": assumptions,
    }
    if len(results) > DETAIL_ROWS:
        # Phrased as an instruction to the agent, not as a fact about the data.
        # The first live run reported "breakdowns were only returned for the top
        # 5" to the user as an UNCERTAINTY -- leaking a token-budget decision
        # into a field reserved for real limits of the evidence.
        #
        # BUT THE SUPPRESSION IS NOW SCOPED. A blanket "never mention this"
        # was correct for scores and the ordering, which ARE complete, and
        # wrong for a claim about one component: asked which hub gets the most
        # snow days, the agent could see the historical line for five hubs,
        # could not fetch the other thirty-five inside its tool-call budget,
        # and was told not to say so. A confident single-factor answer drawn
        # from an eighth of the network is a worse outcome than an honest
        # "top five only".
        payload["note_for_agent"] = (
            f"Breakdowns are included for the top {DETAIL_ROWS} of {len(results)} rows "
            f"only. Every hub is fully scored and ranked, so the scores and the order "
            f"are complete: never report this as a caveat. It limits one thing -- a "
            f"claim about a single COMPONENT (most snow days, highest baseline) can "
            f"only use rows you can see, so call explain_hub_risk for the hubs you "
            f"need or say the comparison covers the top {DETAIL_ROWS} of "
            f"{len(results)}."
        )
    if deps.alerts_error:
        payload["live_alerts_unavailable"] = deps.alerts_error
    return payload


def portfolio_priorities(deps: Deps, region: str | None = None) -> dict[str, Any]:
    """Rank the whole network across all three hazards for investment priority.

    WHY THIS IS A SEPARATE TOOL AND NOT A FLAG ON `rank_hubs_by_risk`. Three
    reasons, all of them structural:

      `hazard` being required and singular on that tool is part of how this
      system stops a cross-hazard comparison being invented. Making it optional
      dissolves the guard.

      One tool returning two different row shapes is the most reliable way to
      get a model quoting the wrong field.

      The forty-row ranking payload is already within ~150 tokens of its budget
      on flood. There is no room in it.
    """
    hubs = deps.registry.hubs
    if region:
        wanted = region.strip().lower()
        hubs = tuple(h for h in hubs if h.region.lower() == wanted)
        if not hubs:
            return {"error": f"No region named {region!r}."}

    entries = rank_portfolio(
        hubs,
        deps.nri,
        deps.engine_alerts(),
        forecasts_by_hub=deps.ensure_forecasts(hubs),
    )

    deps.sources_used.update(
        {
            "FEMA National Risk Index v1.20.0 (December 2025)",
            "NWS active alerts",
            climatology.historical_source(),
        }
    )

    # GROUPED RATHER THAN A FLAT LIST WITH A REPEATED LABEL. "investability":
    # "Invest" on forty rows is ~280 tokens spent on three distinct values, and
    # a grouped payload also makes the tier boundaries impossible to misread.
    buckets: dict[str, dict[str, Any]] = {}
    # Fewer than a ranking gets: a portfolio row already carries the structural
    # score, the exceedance and the driving hazards, so a breakdown on top of
    # that is the widest row in the system.
    detail_rows = max(1, DETAIL_ROWS - 2)
    detail_budget = detail_rows

    for position, entry in enumerate(entries, start=1):
        key = entry.investability or "not_tierable"
        row: dict[str, Any] = {
            "rank": position,
            "hub_id": entry.hub_id,
            "hub": entry.hub_label,
            "driving_hazards": list(entry.driving_hazards),
            "structural_score": entry.structural_score,
            "exceedance": entry.exceedance,
            "risk_score": entry.score,
            "risk_band": entry.band,
        }
        if len(entry.driving_hazards) > 1:
            # Only informative when there is a choice to disclose: with one
            # driving hazard the score obviously belongs to it.
            row["score_is_for_hazard"] = entry.lead_hazard

        alerts = deps.alerts.get(entry.hub_id, ())
        if alerts:
            row["active_alerts"] = [f"{a.event} ({a.severity})" for a in alerts]
        # Detail for the top few candidates only, and only in the top tier --
        # the same reasoning as DETAIL_ROWS in a ranking.
        if entry.investability and detail_budget > 0 and position <= detail_rows:
            row["components"] = list(entry.lead.breakdown())
            row["evidence"] = list(entry.lead.evidence)
            detail_budget -= 1

        bucket = buckets.setdefault(key, {"count": 0, "hubs": []})
        bucket["count"] += 1
        bucket["hubs"].append(row)

    # THE PORTFOLIO-LEVEL DISCLOSURES ONLY, deduplicated once and never per row.
    #
    # The per-hazard methodology notes -- anchors, forecast bounds, individual
    # renormalisations -- came to ~730 tokens across forty hubs and three
    # hazards, and the model does not need them to write this answer. They are
    # not dropped: `assemble` collects every assumption from the engine results
    # into the RESPONSE, which has no token budget, so the reader still sees
    # them. What is trimmed is what the model reads, not what the user is told.
    portfolio_notes = (
        THE_TIER_IS_NOT_TODAYS_RISK,
        THE_TIER_IS_RELATIVE,
        CROSS_HAZARD_COMPARABILITY,
    )
    per_hazard = {
        note
        for entry in entries
        for note in entry.assumptions
        if note not in portfolio_notes
    }
    assumptions = list(portfolio_notes)

    payload: dict[str, Any] = {
        "hub_count": len(entries),
        "scored_by": "deterministic engine (app/scoring/engine.py)",
        "ordered_by": (
            "investment tier first; within a tier, how many hazards put the hub "
            "there, then how far it sits above that hazard's own network cutoff "
            "(exceedance_over_cutoff). Never by raw score across hazards."
        ),
        "tiers": buckets,
        "assumptions": assumptions,
        "further_assumptions": (
            f"{len(per_hazard)} additional per-hazard methodology assumptions "
            f"(anchors, forecast bounds, absent components) travel with the "
            f"response and are shown to the user. Call describe_methodology for "
            f"the model's numbers, or explain_hub_risk for one hub's caveats. Do "
            f"not report their omission here as a limitation of the evidence."
        ),
        "note_for_agent": (
            "This ordering IS the answer to 'which hubs should we invest in'. Do "
            "not reorder it and do not substitute the risk score: a hub can hold "
            "a higher risk_score than the hub above it and still rank lower, "
            "because the tier and the ordering describe persistent exposure "
            "while risk_score includes current conditions. If that happens, say "
            "so -- it is the most useful thing in the answer, not an "
            "inconsistency to smooth over."
        ),
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

    result = score_hub(
        hub, hazard, deps.nri["hubs"][hub.id], deps.engine_alerts().get(hub.id, ()),
        forecast=deps.ensure_forecasts((hub,)).get(hub.id),
    )
    deps.sources_used.update(
        {"FEMA National Risk Index v1.20.0 (December 2025)", "NWS active alerts"}
    )
    if hazard != "hurricane":
        deps.sources_used.add(climatology.historical_source())

    # The hub's rank within its own region, so "why is Dallas high" can say
    # high RELATIVE TO WHAT without a second tool call.
    peers = tuple(h for h in deps.registry.hubs if h.region == hub.region)
    regional = rank_hubs(
        peers, hazard, deps.nri, deps.engine_alerts(),
        forecasts_by_hub=deps.ensure_forecasts(peers),
    )
    position = next(i for i, r in enumerate(regional) if r.hub_id == hub.id) + 1

    payload = _assessment_payload(result, None, deps)
    # THE INVESTMENT READING BELONGS HERE AND NOT ON RANKING ROWS. A single hub
    # has no payload budget worth defending, whereas the forty-row ranking is
    # already within ~150 tokens of its ceiling on flood. This is also where
    # the question that needs it gets asked -- "why is this hub high?" -- and
    # for a hub whose score is entirely live wind, the answer is that nothing
    # structural is driving it at all.
    payload["structural_score"] = result.structural_score
    payload["transient_score"] = result.transient_score
    payload["investment_tier"] = result.investability
    payload["structural_vs_total"] = (
        "no structural component could be measured, so this score is entirely "
        "current conditions"
        if result.structural_score is None
        else f"structural {result.structural_score} vs total {result.score}"
    )
    payload["assumptions"] = list(result.assumptions)
    payload["forecast_next_72h"] = deps.forecasts.get(hub.id)
    payload["rank_within_region"] = f"{position} of {len(regional)} in the {hub.region}"
    payload["grid_cell_provenance"] = climatology.provenance(hub.id)
    return payload


def count_weather_days(
    deps: Deps,
    hub_id: str,
    metric: str,
    year: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
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

    spec = MEASUREMENT_METRICS[metric]
    years = climatology.available_years(hub.id)
    if year and year not in years:
        return {
            "error": f"No data for {year}. This snapshot covers {years[0]}-{years[-1]}.",
            "available_years": list(years),
        }
    if year and (start_date or end_date):
        return {"error": "Pass either year or a start_date/end_date range, not both."}

    try:
        count = climatology.count_days(
            hub.id, spec.variable, spec.threshold,
            year=year, start_date=start_date, end_date=end_date,
            strict=spec.strict, direction=spec.direction,
        )
    except ValueError as exc:
        return {"error": str(exc)}

    if count.days_observed == 0:
        return {
            "error": (
                f"No days in the requested period fall inside the available record "
                f"({years[0]}-01-01 to {years[-1]}-12-31)."
            ),
            "available_years": list(years),
        }
    deps.sources_used.add(climatology.historical_source())

    # ---- the disclosures this count carries, decided HERE -----------------
    # Collected on Deps rather than left to the model to repeat, so a count
    # answer cannot be published without them.
    disclosures = [
        f"{hub.label}: from a ~9 km reanalysis grid cell, not a station at the hub, "
        f"so a matching day means the value was modelled somewhere in the cell."
    ]
    if spec.threshold_is_judgement:
        disclosures.append(
            f"The {spec.threshold:g} {spec.units} threshold is an operational "
            f"judgement, not a measured disruption boundary."
        )
    if year and not climatology.year_is_complete(year):
        disclosures.append(
            f"{year} is incomplete here ({count.days_observed} days), so this is a "
            f"share of the period covered, not of the year -- and for a seasonal "
            f"variable the remaining months are not a random sample of it."
        )
    if count.days_missing:
        disclosures.append(
            f"{count.days_missing} day(s) have no modelled value and are excluded "
            f"from the count and its denominator."
        )
    deps.measurement_assumptions.extend(disclosures)

    payload: dict[str, Any] = {
        "hub": hub.label,
        "metric": metric,
        "metric_description": spec.description,
        # The period ACTUALLY covered, which may be narrower than requested if
        # the range extends past the snapshot. Reported so a mis-resolved
        # relative period ("last six months") is visible in the answer.
        "period": count.period_label,
        "period_requested": year or f"{start_date or 'record start'} to {end_date or 'record end'}",
        "days_matching": count.days_matching,
        "days_observed": count.days_observed,
        "days_missing": count.days_missing,
        "percent_of_observed_days": round(count.percent_of_observed, 1),
        "days_per_year": round(count.days_per_year, 1),
        "evidence": count.evidence(),
        "grid_cell_provenance": climatology.provenance(hub.id),
        "assumptions": disclosures,
    }

    # A metric with a companion returns BOTH counts, unprompted. A user asking
    # "what percentage of days had snowfall" is asking literally and gets the
    # literal answer; giving the operational figure alongside is what lets the
    # agent explain that a trace dusting is not a disruption, without the model
    # having to invent a second threshold.
    if spec.companion:
        other = spec.companion
        o_spec = MEASUREMENT_METRICS[other]
        o_count = climatology.count_days(
            hub.id, o_spec.variable, o_spec.threshold,
            year=year, start_date=start_date, end_date=end_date,
            strict=o_spec.strict, direction=o_spec.direction,
        )
        payload["companion_metric"] = {
            "metric": other,
            "metric_description": o_spec.description,
            "days_matching": o_count.days_matching,
            "percent_of_observed_days": round(o_count.percent_of_observed, 1),
        }
        payload["threshold_note"] = (
            f"Same question, two thresholds, both correct: {spec.description} versus "
            f"{o_spec.description}. Report the one asked for; offer the other as "
            f"context, not a correction."
        )
    return payload


def _resolve_threshold(spec: Metric) -> tuple[float, str]:
    """The threshold to count at, and where it came from.

    Prefers the SCORING threshold when this metric has one, so that a worst
    year is the worst year for the component that actually sizes the
    investment. See `Metric.engine_threshold_key`.
    """
    if spec.engine_threshold_key is None:
        return spec.threshold, "measurement configuration (app/config.py)"
    thresholds = load_weights()["climatology_thresholds"]
    return (
        float(thresholds[spec.engine_threshold_key]),
        f"risk model configuration (app/scoring/weights.yaml: "
        f"{spec.engine_threshold_key})",
    )


def year_by_year(deps: Deps, hub_id: str, metric: str) -> dict[str, Any]:
    """Per-year counts, the worst year on record, and the sample slope.

    WHY THIS IS A SEPARATE TOOL FROM `count_weather_days`. That one answers a
    question about a period the user named. This one answers a question about
    VARIATION BETWEEN periods, which is what an annual investment decision
    turns on: a hub averaging ten disruptive days a year but reaching sixteen
    in its worst year is a different proposition from one that gets ten every
    year, and the mean the scoring engine uses cannot tell them apart.
    """
    hub = deps.hub_or_error(hub_id)
    if isinstance(hub, str):
        return {"error": hub}

    spec = MEASUREMENT_METRICS.get(metric)
    if spec is None:
        return {
            "error": f"Unknown metric {metric!r}. Available: "
            f"{', '.join(sorted(MEASUREMENT_METRICS))}."
        }

    threshold, threshold_source = _resolve_threshold(spec)
    series = climatology.count_days_by_year(
        hub.id,
        spec.variable,
        threshold,
        strict=spec.strict,
        direction=spec.direction,
    )

    deps.sources_used.add(climatology.historical_source())

    # Disclosures travel on the response, not just in this payload:
    # `assemble()` drains `measurement_assumptions` into every answer, so the
    # partial-year and slope caveats cannot be dropped by the model.
    assumptions = list(series.assumptions())
    if spec.threshold_is_judgement:
        assumptions.append(
            f"The {threshold} {spec.units} threshold for {metric} is an "
            f"operational judgement, not a physical boundary. It comes from the "
            f"{threshold_source}, so these counts describe the same exposure the "
            f"risk score's historical component is built from."
        )
    deps.measurement_assumptions.extend(assumptions)

    worst = series.worst_year
    payload: dict[str, Any] = {
        "hub": hub.label,
        "metric": metric,
        "metric_description": spec.description,
        "threshold": threshold,
        "threshold_units": spec.units,
        "threshold_source": threshold_source,
        "years": [
            {
                "year": year.year,
                "days": year.count.days_matching,
                "days_observed": year.count.days_observed,
                "complete_year": year.complete,
            }
            for year in series.years
        ],
        "complete_year_count": len(series.complete_years),
        # None rather than a number when no year is complete: "not comparable
        # yet" is not "zero".
        "worst_complete_year": (
            None
            if worst is None
            else {"year": worst.year, "days": worst.count.days_matching}
        ),
        "mean_days_across_complete_years": (
            None if series.mean_days is None else round(series.mean_days, 1)
        ),
        "slope_days_per_year": (
            None
            if series.slope_days_per_year is None
            else round(series.slope_days_per_year, 2)
        ),
        "evidence": list(series.evidence()),
        "assumptions": assumptions,
        "grid_cell_provenance": climatology.provenance(hub.id),
    }
    if worst is not None and series.mean_days is not None and series.mean_days > 0:
        # COMPUTED HERE SO THE MODEL NEVER DIVIDES. The interesting figure is
        # how far the worst year sits above the typical one, and a model asked
        # for it would otherwise derive it from two numbers on screen -- the
        # same reason the ranking gaps are supplied rather than left to be
        # subtracted.
        payload["worst_year_vs_mean_percent"] = round(
            (worst.count.days_matching / series.mean_days - 1.0) * 100.0, 1
        )
    return payload


# How many individual changes the agent is shown. The full diff can be 120
# rows; a reader asking "what changed" wants the material moves, and
# `change_count` still reports the total so nothing is hidden.
CHANGES_SHOWN = 10


def what_changed(deps: Deps, min_delta: float | None = None) -> dict[str, Any]:
    """Score changes since the last recorded review. READ-ONLY.

    THIS TOOL NEVER ADVANCES THE BASELINE, and that is a correctness property
    rather than a precaution. `alerting.save` is what makes a diff "seen"; if a
    tool the model can call advanced it, a user asking "what changed?" twice
    would be told "nothing" the second time, and the change they were reading
    about would be gone from the record. Advancing the baseline stays with
    `POST /alerts/check` and the scheduled script, which are deliberate acts.

    There is no `commit` parameter for the same reason -- a flag the model can
    set is a flag the model can set wrongly.
    """
    previous = alerting.load_previous()
    if previous is None:
        return {
            "no_baseline": (
                "No previous review has been recorded, so there is nothing to "
                "compare against. Say that plainly: it is not the same as "
                "'nothing changed'. A baseline is created by the scheduled "
                "risk-change job, not by this tool."
            )
        }

    threshold = (
        alerting.DEFAULT_MIN_DELTA if min_delta is None else float(min_delta)
    )

    # The turn's OWN alerts and forecasts, not a second fetch. Two fetches in
    # one turn would let this answer and the same turn's scores disagree about
    # the weather.
    current = alerting.take_snapshot(
        load_config(),
        alerts=deps.alerts,
        forecasts=deps.ensure_forecasts(deps.registry.hubs),
    )
    changes = alerting.diff(previous, current, threshold)

    deps.sources_used.update(
        {
            "FEMA National Risk Index v1.20.0 (December 2025)",
            "NWS active alerts",
            climatology.historical_source(),
        }
    )

    payload: dict[str, Any] = {
        "compared_against": previous.taken_at,
        "measured_at": current.taken_at,
        "change_count": len(changes),
        "band_crossings": sum(1 for c in changes if c.band_changed),
        "min_delta_points": threshold,
        "what_counts_as_a_change": (
            f"a score moving by at least {threshold} points, or crossing a band "
            f"boundary at any size"
        ),
        # `drivers` is deliberately omitted from these rows: it is the full
        # component breakdown, ~40 tokens per change, and explain_hub_risk
        # answers "why did it move" far better than a diff row can.
        "changes": [
            {
                "hub": change.hub,
                "hazard": change.hazard,
                "previous_score": change.previous_score,
                "current_score": change.current_score,
                "delta": change.delta,
                "band_changed": change.band_changed,
                "headline": change.headline(),
            }
            for change in changes[:CHANGES_SHOWN]
        ],
        "assumptions": [
            "An unchanged score is byte-identical between runs, because the "
            "engine is deterministic and no model output reaches it. So a change "
            "reported here reflects changed inputs -- a new alert or a new "
            "forecast -- and never model variation.",
            "Only the CURRENT component can move between runs. The FEMA baseline "
            "and the historical record are frozen snapshots, so a change is "
            "always live weather and never a change in structural exposure or in "
            "a hub's investment tier.",
        ],
    }
    if len(changes) > CHANGES_SHOWN:
        payload["note_for_agent"] = (
            f"Showing the {CHANGES_SHOWN} largest of {len(changes)} changes, by "
            f"absolute move. The count is complete; do not report the trimming "
            f"as a limit of the evidence."
        )
    if deps.alerts_error:
        payload["live_alerts_unavailable"] = deps.alerts_error
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

    rows = []
    for a in alerts:
        scored_for = hazards_scored_by(a.event)
        row = {
            "event": a.event,
            "severity": a.severity,
            "urgency": a.urgency,
            "certainty": a.certainty,
            "headline": a.headline,
            "expires": a.expires,
            "matched_on": a.matched_on,
            # WHICH NUMBER THIS ALERT MOVES, on every row. The feed is fetched
            # per hub, not per hazard, so this list routinely contains events
            # that contribute 0.0 to any score -- and without the label the
            # agent has no way to tell, and presented a Dense Fog Advisory as
            # though it were driving a winter comparison.
            "scored_for_hazards": list(scored_for),
        }
        if not scored_for:
            row["not_scored"] = (
                "This system models winter, hurricane and flood only, and this "
                "event contributes to none of them -- it moves no risk score. "
                "Report it as operational context if it matters to the reader "
                "(fog and visibility affect road freight), never as a reason a "
                "hub scores as it does."
            )
        rows.append(row)

    unscored = sum(1 for r in rows if "not_scored" in r)
    payload: dict[str, Any] = {
        "hub": hub.label,
        "fetched_at": nws.fetched_at(),
        "alert_count": len(alerts),
        "alerts": rows,
    }
    if unscored:
        payload["note_for_agent"] = (
            f"{unscored} of {len(rows)} active alert(s) here contribute to no "
            f"score in this system. Do not present them as drivers of a hub's "
            f"risk, and do not let them stand in for an answer about current "
            f"conditions for a hazard."
        )
    return payload


# WHY THIS TOOL EXISTS: the weights were not reachable from the agent at all.
# Asked what assumptions the score rests on -- the question this design is built
# to answer -- the agent could quote the weights of whichever hub it happened to
# score, could not state a band boundary at any price, and could not give the
# historical anchor unless a hazard was scored first. Rule 1 forbids stating a
# number no tool returned, so the compliant answer to the most audit-relevant
# question in the system was "I do not have that".
#
# Everything is read from weights.yaml at call time, so the answer cannot drift
# from the config the engine actually uses. The strings are deliberately terse:
# this payload is read by a model, and prose in a tool result is paid for on
# every call that returns it.
def describe_methodology(deps: Deps, hazard: str | None = None) -> dict[str, Any]:
    """The scoring model: component weights, the FEMA hazards behind the
    baseline, the historical anchor, forecast bounds, alert severities, band
    boundaries, and the status of those numbers. Pass a hazard for its numbers
    plus its alert event list; omit it for all three."""
    config = load_weights()
    wanted = hazard.strip().lower() if hazard else None
    if wanted and wanted not in config["hazards"]:
        return {"error": f"Unsupported hazard {hazard!r}. Supported: {', '.join(HAZARDS)}."}

    bands: list[dict[str, Any]] = []
    lower = 0.0
    for band in config["bands"]:
        upper = float(band["max"])
        # The last band's configured maximum is 101 so a score of exactly 100
        # lands somewhere; reported as 100, the real top of the scale.
        bands.append({
            "band": band["label"],
            "scores": f"{lower:g}-{upper:g}" if upper <= 100 else f"{lower:g}-100",
        })
        lower = upper

    def describe(hazard_config: dict[str, Any]) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "label": hazard_config["label"],
            "weights": {k: float(v) for k, v in hazard_config["weights"].items()},
            "baseline_nri_hazards": list(hazard_config["baseline_hazards"]),
            "baseline_combined_by": hazard_config["baseline_combine"],
        }
        if hazard_config.get("historical_metric"):
            entry["historical_metric"] = hazard_config["historical_metric"]
            entry["historical_anchor_days_per_year"] = float(
                hazard_config["anchor_days_per_year"]
            )
        else:
            entry["historical"] = None
            entry["historical_excluded_because"] = str(
                hazard_config.get("methodology_note", "")
            ).strip()
        entry["forecast_metric"] = hazard_config.get("forecast_metric")
        entry["forecast_scores_0_below"] = hazard_config.get("forecast_floor")
        entry["forecast_scores_100_at"] = hazard_config.get("forecast_anchor")
        # The full list only when one hazard was asked for: three lists of event
        # names is most of this payload and nobody reads them comparatively.
        entry["alert_event_types"] = (
            list(hazard_config["alert_events"])
            if wanted
            else len(hazard_config["alert_events"])
        )
        return entry

    names = [wanted] if wanted else list(config["hazards"])
    deps.sources_used.add("Risk model configuration (app/scoring/weights.yaml)")

    return {
        "formula": (
            "score = sum(component value x weight) / sum(weights present); each "
            "component is 0-100 before weighting. baseline = FEMA NRI county "
            "exposure; historical = qualifying days/year from the reanalysis, "
            "linear to the anchor then clipped at 100; current = max(NWS alert "
            "severity, quantitative 72h forecast)."
        ),
        "hazards": {name: describe(config["hazards"][name]) for name in names},
        # What "High" means as a number -- the one figure no tool returned, so
        # the agent could name a band but never say where it starts.
        "bands": bands,
        "band_boundaries": "inclusive lower, exclusive upper",
        "daily_thresholds": {
            k: float(v) for k, v in config["climatology_thresholds"].items()
        },
        "alert_severity_scores": dict(config["alert_severity_scores"]),
        # WITHOUT THIS THE AGENT CANNOT EXPLAIN A TIER. Rule 1 forbids it from
        # stating any figure no tool returned, so a question like "what makes a
        # hub Invest?" had no answerable source.
        "investment_tiers": {
            "why_a_second_reading_exists": (
                "The risk score blends standing exposure with live conditions, "
                "which is right for 'how exposed is this hub now' and wrong for "
                "'where should next year's resilience budget go' -- a storm "
                "passing through raises a hub it will leave. So a structural "
                "score is computed from the baseline and historical components "
                "alone, and the tier is derived from that."
            ),
            "structural_score": (
                "baseline and historical components only, renormalised over the "
                "weights present. Uses no alert and no forecast, so it does not "
                "move with the weather."
            ),
            "transient_score": "the current component alone: alerts and the 72h forecast.",
            "Invest": (
                f"structural score in this network's top "
                f"1-in-{int(config['investability']['invest_top_one_in'])} for "
                f"that hazard, hubs tied at the boundary included"
            ),
            "Watch": (
                "outside the Invest group, but current conditions in "
                + " or ".join(config["investability"]["watch_transient_bands"])
                + " -- something is happening at a hub the structural record "
                "would not otherwise fund"
            ),
            "Low": "neither",
            "not_tierable": (
                "no structural component could be measured, so there is no "
                "persistent exposure to invest against. True for the eight hubs "
                "FEMA models no hurricane risk for, whose entire hurricane score "
                "is therefore the live wind reading."
            ),
            "structural_cutoffs_in_use": {
                name: structural_cutoff(name) for name in names
            },
            "why_relative_and_not_an_absolute_band": (
                "Measured: an absolute rule (structural in High or Very High) "
                "tiered 88 of 112 hub-hazard pairs as Invest. FEMA's NRI is "
                "loss-weighted and scored against every US county, so a network "
                "of major metros sits in the national top tail by construction "
                "-- inland flooding across these hubs has a median of 98.5 and a "
                "minimum of 72.3. Ranking within the network discriminates "
                "evenly instead, and states the claim the decision needs, since "
                "the analyst is choosing among these hubs."
            ),
            "what_the_tier_is_not": (
                "It is a RELATIVE standing, not an absolute judgement: an Invest "
                "hub is not thereby claimed to be at risk in absolute terms, and "
                "adding or removing hubs can move the boundary. It also ranks "
                "exposure, not return on investment -- this system has no cost, "
                "throughput or downtime data."
            ),
        },
        "missing_data_policy": (
            "Unknown alert severity is excluded from the current component, not "
            "scored 0; no alerts at all IS a real 0. A component that cannot be "
            "measured is dropped and the remaining weights renormalised over the "
            "weights present, never imputed as 0, and the score says so."
        ),
        "status_of_these_numbers": (
            "PROTOTYPE ASSUMPTIONS, NOT MEASURED COEFFICIENTS. None was fitted "
            "against observed hub downtime; no such dataset was available. They "
            "live in a config file so a reviewer can disagree and re-run."
        ),
        "what_the_score_is_not": (
            "A 0-100 comparative exposure index, not a prediction. It does not "
            "estimate delays, closures, cost or tonnage, and no data of that kind "
            "was used. Scores are comparable within a hazard; across hazards the "
            "weightings differ, so winter and flood are not one measurement."
        ),
        "historical_source": climatology.historical_source(),
        "hazards_supported": list(HAZARDS),
    }
