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
from app.hubs import load_hubs
from app.http_client import UpstreamError
from app.scoring.engine import Hazard, rank_hubs
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


def model_settings() -> AnthropicModelSettings:
    """Determinism, caching and bounds, in one place.

    temperature=0      the same question must produce the same answer. This is
                       a decision-support tool: an analyst who asks twice and
                       gets two rankings cannot trust either. It also makes the
                       eval suite mean something -- a failure is a real
                       failure, not sampling noise.

    anthropic_effort   'low'. The model's job is intent + tool selection +
                       short synthesis over structured results. The reasoning
                       is in the scoring engine, and paying for extended
                       thinking to re-derive what a tool already computed is
                       the definition of waste here.

    cache_*            the instructions (~970 tokens) and the five tool
                       schemas are byte-identical on every request, and in a
                       conversation the earlier messages are too. Caching all
                       three turns the fixed prefix from a per-request charge
                       into a written-once one.

    max_tokens         the output is a structured draft with short prose. A
                       cap stops a runaway generation costing real money.
    """
    return AnthropicModelSettings(
        temperature=0.0,
        max_tokens=2000,
        anthropic_effort="low",
        anthropic_cache_instructions=True,
        anthropic_cache_tool_definitions=True,
        anthropic_cache_messages=True,
    )


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
        model_settings=model_settings(),
        retries=2,
    )

    @agent.tool
    def list_hubs(ctx: RunContext[Deps], region: str | None = None) -> dict[str, Any]:
        """List the distribution hubs in the network, optionally filtered to one
        region (Midwest, Northeast, South or West)."""
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
        rather than judging risk yourself."""
        return tool_impl.rank_hubs_by_risk(ctx.deps, hazard, region, hub_ids)  # type: ignore[arg-type]

    @agent.tool
    def explain_hub_risk(ctx: RunContext[Deps], hub_id: str, hazard: str) -> dict[str, Any]:
        """Full component-by-component breakdown of one hub's score for one
        hazard, including which component contributed most and how the hub
        ranks within its region."""
        return tool_impl.explain_hub_risk(ctx.deps, hub_id, hazard)  # type: ignore[arg-type]

    @agent.tool
    def count_weather_days(
        ctx: RunContext[Deps], hub_id: str, metric: str, year: str | None = None
    ) -> dict[str, Any]:
        """Count days matching a named metric from the 2021-2025 record. Metrics:
        any_snowfall, disruptive_snowfall, heavy_precipitation, damaging_wind,
        freezing. Pass a four-digit year to restrict the period. Use this for
        direct measurement questions rather than risk questions."""
        return tool_impl.count_weather_days(ctx.deps, hub_id, metric, year)

    @agent.tool
    def get_hub_alerts(ctx: RunContext[Deps], hub_id: str) -> dict[str, Any]:
        """Active National Weather Service alerts for one hub right now."""
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


def assemble(draft: AgentDraft, deps: Deps) -> AgentResponse:
    """Join the engine's numbers onto the model's prose."""
    assessments: list[HubAssessment] = []
    assumptions: list[str] = []
    uncertainty: list[str] = list(draft.caveats)

    if draft.hazard and draft.intent in ("rank", "compare", "explain"):
        hazard: Hazard = draft.hazard
        if draft.hub_ids:
            hubs = tuple(
                h for h in (deps.registry.by_id(i) for i in draft.hub_ids) if h is not None
            )
        elif draft.region:
            wanted = draft.region.strip().lower()
            hubs = tuple(h for h in deps.registry.hubs if h.region.lower() == wanted)
        else:
            hubs = deps.registry.hubs

        if hubs:
            ranked = rank_hubs(hubs, hazard, deps.nri, deps.engine_alerts())
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
                        main_drivers=((driver.name,) if driver else ()),
                        component_breakdown=result.breakdown(),
                        evidence=result.evidence,
                        active_alerts=tuple(f"{a.event} ({a.severity})" for a in alerts),
                    )
                )
                assumptions.extend(result.assumptions)

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
    usage = result.usage()
    return TurnResult(
        response=assemble(result.output, deps),
        messages=list(result.all_messages()),
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cached_tokens=getattr(usage, "cache_read_tokens", 0) or 0,
        requests=getattr(usage, "requests", 0) or 0,
    )
