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
    # Quantitative NWS forecasts, fetched lazily for the hubs actually being
    # scored -- 40 gridpoint calls on every turn would put the whole network
    # on the critical path of a question about two hubs.
    forecasts: dict[str, dict[str, Any]] = field(default_factory=dict)
    sources_used: set[str] = field(default_factory=set)

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
        deps.sources_used.add("Open-Meteo ECMWF IFS reanalysis, 2021-2025")

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
        # Phrased as an instruction to the agent, not as a fact about the
        # data. The first live run reported "breakdowns were only returned for
        # the top 5" to the user as an UNCERTAINTY -- leaking a token-budget
        # decision into a field reserved for real limits of the evidence.
        payload["note_for_agent_do_not_report"] = (
            f"Component breakdowns are included for the top {DETAIL_ROWS} rows to "
            f"keep this payload small. Every hub is fully scored and ranked. Call "
            f"explain_hub_risk for any other hub's breakdown. This is an internal "
            f"retrieval detail: never present it to the user as a caveat, an "
            f"uncertainty, or a limitation of the analysis."
        )
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
        deps.sources_used.add("Open-Meteo ECMWF IFS reanalysis, 2021-2025")

    # The hub's rank within its own region, so "why is Dallas high" can say
    # high RELATIVE TO WHAT without a second tool call.
    peers = tuple(h for h in deps.registry.hubs if h.region == hub.region)
    regional = rank_hubs(
        peers, hazard, deps.nri, deps.engine_alerts(),
        forecasts_by_hub=deps.ensure_forecasts(peers),
    )
    position = next(i for i, r in enumerate(regional) if r.hub_id == hub.id) + 1

    payload = _assessment_payload(result, None, deps)
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

    variable, threshold, strict, description = MEASUREMENT_METRICS[metric]
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
            hub.id, variable, threshold,
            year=year, start_date=start_date, end_date=end_date, strict=strict,
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
    deps.sources_used.add("Open-Meteo ECMWF IFS reanalysis, 2021-2025")

    payload: dict[str, Any] = {
        "hub": hub.label,
        "metric": metric,
        "metric_description": description,
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
            hub.id, o_var, o_threshold,
            year=year, start_date=start_date, end_date=end_date, strict=o_strict,
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
