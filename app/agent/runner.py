"""Wiring: the agent, its tools, and the assembly that joins real scores back in.

The flow for one turn:

    question
      -> agent (PydanticAI, Anthropic)   chooses tools, fills AgentDraft
      -> tools                           deterministic engine + live alerts
      -> AgentDraft                      validated; carries NO scores
      -> assemble()                      re-runs the engine for the named hubs
                                         and attaches ITS numbers
      -> AgentResponse

`assemble` re-running the engine is deliberate and is not redundant work. It
means the numbers in the response are produced by the application after the
model has finished, from the hubs and hazard the model named -- not lifted out
of a tool result the model could have paraphrased. The model's influence ends
at "which hubs, which hazard".
"""

from __future__ import annotations

import logging
import os
from datetime import date
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.messages import ModelRequest
from pydantic_ai.models.anthropic import AnthropicModelSettings

from app.agent.contract import AgentDraft, AgentResponse, HubAssessment
from app.agent.prompt import SYSTEM_PROMPT
from app.agent.tools import Deps
from app.agent import tools as tool_impl
from app.config import load_config
from app.hubs import Hub, load_hubs
from app.http_client import UpstreamError
from app.scoring import climatology
from app.scoring.engine import hazards_scored_by, rank_hubs, rank_portfolio
from app.tools import nws

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.environ.get("WRA_MODEL", "anthropic:claude-sonnet-5")

# How many previous turns are replayed to the model. Message history is the
# term that GROWS: every turn resends every earlier turn's tool results, so an
# uncapped conversation pays for its own transcript again on each question.
# Three turns covers the follow-up patterns this system is for ("what about
# flooding only?", "which component contributed most?") without carrying a
# whole session forever. The full transcript is still kept for display; this
# bounds only what the model is charged for.
HISTORY_TURNS = int(os.environ.get("WRA_HISTORY_TURNS", "3"))


# Models observed to reject `anthropic_effort` with
#   400 invalid_request_error: "This model does not support the effort parameter."
# Kept as a substring list rather than inferred from a version number, because
# the capability is a property of the model, not something derivable from its
# name -- and a wrong guess here fails every request rather than degrading.
_NO_EFFORT_PARAMETER = ("haiku",)


def model_settings(model: str | None = None) -> AnthropicModelSettings:
    """Determinism, caching and bounds, in one place.

    temperature=0      Set, but VERIFY BEFORE RELYING ON IT: claude-sonnet-5
                       rejects sampling parameters outright (pydantic-ai warns
                       "Sampling parameters ['temperature'] are not supported
                       by 'claude-sonnet-5'. These settings will be ignored").
                       It is kept because it takes effect on models that do
                       honour it, and costs nothing on models that do not.

                       So prose is NOT bit-reproducible on this model. What IS
                       reproducible is everything that matters for a decision:
                       the scores, the ranking and the components all come from
                       the deterministic engine, which never sees the model. Two
                       runs can word an answer differently; they cannot rank
                       hubs differently. The eval suite is written against the
                       structured fields for exactly this reason.

    anthropic_effort   'low'. The model's job is intent + tool selection +
                       short synthesis over structured results. The reasoning
                       is in the scoring engine, and paying for extended
                       thinking to re-derive what a tool already computed is
                       the definition of waste here.

    cache_*            the instructions (~1,280 tokens) and the six tool
                       schemas are byte-identical on every request, and in a
                       conversation the earlier messages are too. Caching all
                       three turns the fixed prefix from a per-request charge
                       into a written-once one -- which is what makes the
                       instructions affordable at all: a cache read is a
                       fraction of the input price, so prose in the PROMPT is
                       paid for once per conversation while prose in a TOOL
                       RESULT is paid for on every call that returns it. That
                       asymmetry is why the tool payloads here are terse and
                       the rules are allowed a full sentence.

    max_tokens         the output is a structured draft with short prose. A
                       cap stops a runaway generation costing real money.
    """
    settings = AnthropicModelSettings(
        temperature=0.0,
        max_tokens=2000,
        anthropic_cache_instructions=True,
        anthropic_cache_tool_definitions=True,
        anthropic_cache_messages=True,
    )
    target = (model or DEFAULT_MODEL).lower()
    if not any(marker in target for marker in _NO_EFFORT_PARAMETER):
        settings["anthropic_effort"] = "low"
    return settings


def usage_limits() -> UsageLimits:
    """A hard ceiling on one turn.

    A tool-calling agent's failure mode is a loop: it calls a tool, dislikes
    the result, calls it again. Without a bound that is unmetered spend on a
    question nobody is still waiting for. Eight tool calls is generous for the
    worst legitimate case here (resolve names, then rank, then explain two
    hubs); past that something is wrong and stopping is correct.
    """
    return UsageLimits(request_limit=10, tool_calls_limit=8)


def trim_history(history: list[Any] | None) -> list[Any] | None:
    """Keep the most recent turns only.

    Counted in ModelRequest boundaries, which is where a turn begins, so a
    request and the response that answered it are never split apart -- a
    dangling tool call with no result is a malformed conversation, not a
    cheaper one.
    """
    if not history:
        return None
    boundaries = [
        i for i, message in enumerate(history) if isinstance(message, ModelRequest)
    ]
    if len(boundaries) <= HISTORY_TURNS:
        return history
    return history[boundaries[-HISTORY_TURNS] :]


def build_agent(model: str | None = None) -> Agent[Deps, AgentDraft]:
    agent = Agent(
        model or DEFAULT_MODEL,
        output_type=AgentDraft,
        deps_type=Deps,
        instructions=SYSTEM_PROMPT,
        model_settings=model_settings(model or DEFAULT_MODEL),
        retries=2,
    )

    @agent.instructions
    def temporal_context() -> str:
        """Today's date, and what the record actually covers.

        WITHOUT THIS THE MODEL GUESSES THE YEAR. Asked "what percentage of
        days in Denver LAST YEAR had snowfall?", the first live run answered
        for 2024 -- a model has no clock, so "last year" resolved against its
        training data rather than against today. The answer was internally
        consistent and quietly about the wrong year, which is the worst shape
        a wrong answer can take.

        Separate from a static prompt because it changes every day, and a
        hardcoded date would be wrong tomorrow.
        """
        from app.scoring.climatology import load_history

        window = load_history()["window"]
        today = date.today()
        return (
            f"TODAY'S DATE is {today.isoformat()}. The current year is "
            f"{today.year}, so 'last year' means {today.year - 1} and "
            f"'this year' means {today.year}.\n"
            f"The historical record available to you covers {window['start']} "
            f"to {window['end']} inclusive. A question about a period outside "
            f"that range cannot be answered from this data -- say so rather "
            f"than silently answering about a year you do have."
        )

    @agent.tool
    def list_hubs(ctx: RunContext[Deps], region: str | None = None) -> dict[str, Any]:
        """List the distribution hubs in the network, optionally filtered to one
        region (Midwest, Northeast, South or West)."""
        ctx.deps.tools_called.append("list_hubs")
        return tool_impl.list_hubs(ctx.deps, region)

    @agent.tool
    def rank_hubs_by_risk(
        ctx: RunContext[Deps],
        hazard: str,
        region: str | None = None,
        hub_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Score and rank hubs for one hazard (winter, hurricane or flood),
        highest risk first. Optionally limit to a region or to specific hub ids.
        The scores and the ordering are computed deterministically; use this
        rather than judging risk yourself.

        Each row carries gap_to_leader and gap_to_next. Quote those figures for
        any "how much higher/lower" comparison rather than subtracting scores
        yourself -- every number you state must come from a tool."""
        ctx.deps.tools_called.append(f"rank_hubs_by_risk({hazard})")
        return tool_impl.rank_hubs_by_risk(ctx.deps, hazard, region, hub_ids)  # type: ignore[arg-type]

    @agent.tool
    def portfolio_priorities(
        ctx: RunContext[Deps], region: str | None = None
    ) -> dict[str, Any]:
        """Rank the whole network across ALL THREE hazards for resilience
        investment priority, grouped into tiers. Optionally restrict to one
        region.

        THIS is the tool for the investment question -- "which hubs should we
        invest in", "where should the resilience budget go", "which handful
        should we prioritise this year", "what are our biggest exposures". Use
        it instead of calling rank_hubs_by_risk three times and merging: that
        merge is a cross-hazard comparison you must not make, and this tool has
        already made the only comparison that is valid.

        It groups hubs by a tier computed from STRUCTURAL exposure alone -- the
        FEMA baseline and the historical record, with live conditions excluded --
        because a resilience upgrade acts on exposure that persists. So a hub can
        carry a higher risk_score than one ranked above it. That is not an error:
        report it, because it is usually the most useful thing in the answer.

        Use rank_hubs_by_risk instead when the question names a single hazard."""
        ctx.deps.tools_called.append(f"portfolio_priorities({region or 'all'})")
        return tool_impl.portfolio_priorities(ctx.deps, region)

    @agent.tool
    def explain_hub_risk(ctx: RunContext[Deps], hub_id: str, hazard: str) -> dict[str, Any]:
        """Full component-by-component breakdown of one hub's score for one
        hazard, including which component contributed most and how the hub
        ranks within its region."""
        ctx.deps.tools_called.append(f"explain_hub_risk({hub_id}/{hazard})")
        return tool_impl.explain_hub_risk(ctx.deps, hub_id, hazard)  # type: ignore[arg-type]

    @agent.tool
    def count_weather_days(
        ctx: RunContext[Deps],
        hub_id: str,
        metric: str,
        year: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        """Count days matching a named metric from the historical reanalysis
        record, whose window is given in your instructions. Metrics:
        any_snowfall, disruptive_snowfall, any_precipitation,
        heavy_precipitation, damaging_wind, freezing.

        Pick the one matching what was ASKED: "did it rain" is
        any_precipitation, "heavy rain" is heavy_precipitation. Metrics with a
        companion return both figures.

        Restrict the period with EITHER a four-digit year OR an inclusive
        start_date/end_date pair (YYYY-MM-DD), never both. Use the range form
        for relative periods such as a winter season or "the last six months",
        resolving them against today's date given in your instructions. The
        result reports the period it actually covered, which may be narrower
        than you asked for.

        Use this for direct measurement questions rather than risk questions."""
        ctx.deps.tools_called.append(f"count_weather_days({hub_id}/{metric})")
        return tool_impl.count_weather_days(
            ctx.deps, hub_id, metric, year, start_date, end_date
        )

    @agent.tool
    def year_by_year(ctx: RunContext[Deps], hub_id: str, metric: str) -> dict[str, Any]:
        """Year-by-year counts for one hub and metric: every year in the record,
        the worst complete year, how far that year sat above the typical one,
        and the slope across the sample. Same metric names as
        count_weather_days.

        Use it when the question is about VARIATION BETWEEN YEARS rather than a
        total for one period -- the worst year on record, whether something is
        getting more frequent, how unusual a bad year was. A resilience
        investment is sized against the bad year, and the risk score's
        historical component reports only the multi-year average, so no other
        tool answers this.

        The partial year at the end of the record is returned but excluded from
        the worst-year and slope figures. The slope is the direction of this
        five-year sample, not a forecast; report it with the caveat that comes
        back with it, and never treat it as a prediction."""
        ctx.deps.tools_called.append(f"year_by_year({hub_id}/{metric})")
        return tool_impl.year_by_year(ctx.deps, hub_id, metric)

    @agent.tool
    def describe_methodology(
        ctx: RunContext[Deps], hazard: str | None = None
    ) -> dict[str, Any]:
        """How the score is built: component weights, baseline FEMA hazards,
        historical anchor, forecast bounds, alert severities, band boundaries,
        and the status of those numbers.

        Use it for any question about the model rather than about a hub -- the
        assumptions, the weights, what "High" means numerically, what the score
        does not claim. No other tool returns these figures. Pass a hazard for
        its numbers plus its alert events; omit it for all three."""
        ctx.deps.tools_called.append(f"describe_methodology({hazard or 'all'})")
        return tool_impl.describe_methodology(ctx.deps, hazard)

    @agent.tool
    def what_changed(
        ctx: RunContext[Deps], min_delta: float | None = None
    ) -> dict[str, Any]:
        """What has changed since the last recorded risk review: score moves and
        band crossings, largest first.

        Use it for "what changed", "anything new since last time", "what moved".

        Only the current component can move between runs -- the FEMA baseline and
        the historical record are frozen snapshots -- so every change here is
        live weather. It is NOT a change in structural exposure and never a
        change in a hub's investment tier; do not describe one as if it were.

        If no previous review exists the result says so, and that is different
        from nothing having changed. Say which one it is."""
        ctx.deps.tools_called.append("what_changed")
        return tool_impl.what_changed(ctx.deps, min_delta)

    @agent.tool
    def get_hub_alerts(ctx: RunContext[Deps], hub_id: str) -> dict[str, Any]:
        """Active National Weather Service alerts for one hub right now."""
        ctx.deps.tools_called.append(f"get_hub_alerts({hub_id})")
        return tool_impl.get_hub_alerts(ctx.deps, hub_id)

    return agent


# ------------------------------------------------------------- assembling --


def build_deps(nri: dict[str, Any]) -> Deps:
    """One Deps per turn. Live alerts are fetched once, here.

    A failure to reach the NWS is recorded, never swallowed into an empty
    alert set: "no alerts" and "we could not check" must not look the same to
    the engine or to the user.
    """
    deps = Deps(nri=nri, registry=load_hubs())
    try:
        deps.alerts = nws.fetch_active_alerts(load_config())
    except UpstreamError as exc:
        deps.alerts_error = (
            f"Live NWS alerts could not be retrieved ({exc.detail}). Scores below "
            "are based on baseline and historical components only."
        )
        logger.warning("alert fetch failed: %s", exc)
    return deps


def _label_alerts(alerts: tuple[Any, ...], hazard: str) -> tuple[str, ...]:
    """Alert lines for a row, each saying whether it moves THIS hazard's score.

    The alert feed is fetched per hub, so a winter row's alerts can include a
    Dense Fog Advisory, which contributes to no hazard at all. Listing it
    unlabelled next to a winter score implies it is one of the score's inputs.
    Labelled, it stays visible -- fog matters to a driver -- without pretending
    to be a driver of the number beside it.
    """
    lines = []
    for alert in alerts:
        scored = hazards_scored_by(alert.event)
        if hazard in scored:
            suffix = ""
        elif scored:
            suffix = f" -- scores {'/'.join(scored)}, not {hazard}"
        else:
            suffix = " -- not scored, operational context only"
        lines.append(f"{alert.event} ({alert.severity}){suffix}")
    return tuple(lines)


def _selected_hubs(draft: AgentDraft, deps: Deps) -> tuple[Hub, ...]:
    """The hubs an assembly covers: those named, else those in the named
    region, else the whole network."""
    if draft.hub_ids:
        return tuple(
            h for h in (deps.registry.by_id(i) for i in draft.hub_ids) if h is not None
        )
    if draft.region:
        wanted = draft.region.strip().lower()
        return tuple(h for h in deps.registry.hubs if h.region.lower() == wanted)
    return deps.registry.hubs


def assemble(draft: AgentDraft, deps: Deps) -> AgentResponse:
    """Join the engine's numbers onto the model's prose."""
    assessments: list[HubAssessment] = []
    assumptions: list[str] = []
    uncertainty: list[str] = list(draft.caveats)

    hubs = _selected_hubs(draft, deps)

    # A PORTFOLIO BRANCH IS MANDATORY, NOT OPTIONAL. A portfolio draft names no
    # hazard, so it falls straight through the gate below and produces zero
    # assessments -- which passes `check_score_integrity` and `check_disclosure`
    # vacuously, renders nothing in the UI, and leaves every number in the prose
    # ungrounded, because the allowed set is built from assessments.
    if draft.intent == "portfolio":
        # THE SHORTLIST IS THE ENGINE'S, NOT THE MODEL'S SELECTION FROM IT.
        # `hub_ids` is ignored here even though the schema tells the model to
        # leave it empty -- asked for "the top three", Sonnet filled it with
        # three hubs, and honouring that would have re-ranked the portfolio over
        # only those three. The ordering would still have been the engine's, but
        # WHICH hubs reached the table would have been the model's, and a
        # cherry-picked subset in the right relative order passes every ordering
        # check we have. A region filter is honoured because it narrows the
        # question rather than answering it.
        hubs = (
            tuple(
                h
                for h in deps.registry.hubs
                if h.region.lower() == draft.region.strip().lower()
            )
            if draft.region
            else deps.registry.hubs
        )

    if draft.intent == "portfolio" and hubs:
        # A portfolio always reads all three hazards, so all three sources are
        # attributed unconditionally.
        deps.sources_used.update(
            {
                "FEMA National Risk Index v1.20.0 (December 2025)",
                "NWS active alerts",
                climatology.historical_source(),
            }
        )
        entries = rank_portfolio(
            hubs, deps.nri, deps.engine_alerts(),
            forecasts_by_hub=deps.ensure_forecasts(hubs),
        )
        for position, entry in enumerate(entries, start=1):
            lead = entry.lead
            assessments.append(
                HubAssessment(
                    hub_id=entry.hub_id,
                    hub=entry.hub_label,
                    region=entry.region,
                    # The hazard the score belongs to, so a reader can see which
                    # of the driving hazards the number describes.
                    hazard=entry.lead_hazard,
                    risk_score=entry.score,
                    risk_band=entry.band,
                    rank=position,
                    main_drivers=((lead.top_driver.name,) if lead.top_driver else ()),
                    component_breakdown=lead.breakdown(),
                    evidence=lead.evidence,
                    active_alerts=_label_alerts(
                        deps.alerts.get(entry.hub_id, ()), entry.lead_hazard
                    ),
                    structural_score=entry.structural_score,
                    investability=entry.investability,
                    # gap_to_leader and gap_to_next stay None here: see the
                    # contract.
                )
            )
            assumptions.extend(entry.assumptions)

    elif draft.hazards and draft.intent in ("rank", "compare", "explain"):
        # ONE RANKING PER HAZARD. A question can legitimately span several --
        # "hurricane and flood exposure" is one of the assignment's own
        # examples, and "why is this hub risky?" spans all three. Assembling
        # only the first would leave the rest of the answer's numbers with no
        # corresponding assessment: unverifiable to a reader, and invisible to
        # the groundedness check that is supposed to catch exactly that.
        for hazard in draft.hazards:
            if not hubs:
                break
            # Attribute the sources THIS assembly reads, not the ones the model
            # happened to call. A follow-up answered from conversation context
            # makes no tool call, yet the scores below are still computed here
            # from the NRI snapshot, the climatology and the live alerts --
            # reporting no sources for them would understate the provenance of
            # numbers the response is asserting.
            deps.sources_used.update(
                {
                    "FEMA National Risk Index v1.20.0 (December 2025)",
                    "NWS active alerts",
                }
            )
            if hazard != "hurricane":
                deps.sources_used.add(climatology.historical_source())

            ranked = rank_hubs(
                hubs, hazard, deps.nri, deps.engine_alerts(),
                forecasts_by_hub=deps.ensure_forecasts(hubs),
            )
            leader = ranked[0].score if ranked else 0.0
            for position, result in enumerate(ranked, start=1):
                hub = deps.registry.by_id(result.hub_id)
                alerts = deps.alerts.get(result.hub_id, ())
                driver = result.top_driver
                assessments.append(
                    HubAssessment(
                        hub_id=result.hub_id,
                        hub=result.hub_label,
                        region=hub.region if hub else "",
                        hazard=result.hazard,
                        risk_score=result.score,
                        risk_band=result.band,
                        rank=position if len(ranked) > 1 else None,
                        gap_to_leader=round(leader - result.score, 1),
                        gap_to_next=(
                            round(result.score - ranked[position].score, 1)
                            if position < len(ranked)
                            else None
                        ),
                        main_drivers=((driver.name,) if driver else ()),
                        component_breakdown=result.breakdown(),
                        evidence=result.evidence,
                        active_alerts=_label_alerts(alerts, result.hazard),
                        # CARRIED ON A RISK ROW TOO, while `investability` is
                        # not. The tier is an investment judgement and would
                        # invite a ranking to be read as a shortlist; the
                        # structural score is just a decomposition of the score
                        # above it.
                        #
                        # It has to be here because `explain_hub_risk` shows it
                        # to the model: the eval caught the agent quoting Dallas's
                        # 99.7 with nothing in the response to audit it against.
                        # A figure the model is shown must be a figure the
                        # response carries.
                        structural_score=result.structural_score,
                    )
                )
                assumptions.extend(result.assumptions)

    # THE MEASUREMENT PATH GETS THE SAME TREATMENT AS THE SCORING PATH. The
    # engine's assumptions are attached above because the application recomputed
    # the scores; a count's assumptions are attached here because the
    # application recorded them when the count was taken. Either way the
    # disclosure does not depend on the model choosing to repeat it -- which
    # matters most on this path, where the number is a bare percentage a reader
    # has no way to sanity-check.
    assumptions.extend(deps.measurement_assumptions)

    if deps.alerts_error:
        uncertainty.append(deps.alerts_error)

    # De-duplicate while preserving order: the same assumption fires for every
    # hub in a 40-hub ranking, and forty copies of one sentence is noise.
    assumptions = list(dict.fromkeys(assumptions))
    uncertainty = list(dict.fromkeys(uncertainty))

    return AgentResponse(
        answer=draft.answer,
        intent=draft.intent,
        interpretation_of_question=draft.interpretation_of_question,
        assessments=tuple(assessments),
        interpretation=tuple(draft.interpretation),
        assumptions=tuple(assumptions),
        uncertainty=tuple(uncertainty),
        sources=tuple(sorted(deps.sources_used)),
        clarification_question=draft.clarification_question,
        out_of_scope_reason=draft.out_of_scope_reason,
    )


@dataclass
class TurnResult:
    response: AgentResponse
    messages: list[Any]
    # The exact evidence this turn was scored from -- the alerts and forecasts
    # fetched once at its start. Exposed so a caller can recompute the scores
    # from the SAME observations. With a live forecast in the model, a
    # verifier that re-fetches is measuring whether the weather changed
    # between two calls, not whether the agent altered a number.
    deps: Deps | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    requests: int = 0

    @property
    def cache_hit_rate(self) -> float:
        """Share of input tokens served from cache rather than charged in
        full. Reported so the caching is verifiable rather than assumed."""
        total = self.input_tokens + self.cached_tokens
        return 0.0 if total == 0 else self.cached_tokens / total * 100.0


def run_turn(
    question: str,
    nri: dict[str, Any],
    history: list[Any] | None = None,
    agent: Agent[Deps, AgentDraft] | None = None,
) -> TurnResult:
    """One conversational turn. `history` carries follow-up context."""
    agent = agent or build_agent()
    deps = build_deps(nri)
    result = agent.run_sync(
        question,
        deps=deps,
        message_history=trim_history(history),
        usage_limits=usage_limits(),
    )
    usage = result.usage
    return TurnResult(
        deps=deps,
        response=assemble(result.output, deps),
        messages=list(result.all_messages()),
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cached_tokens=getattr(usage, "cache_read_tokens", 0) or 0,
        requests=getattr(usage, "requests", 0) or 0,
    )
