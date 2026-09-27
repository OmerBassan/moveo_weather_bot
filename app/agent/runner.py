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

from pydantic_ai import Agent, RunContext

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


def build_agent(model: str | None = None) -> Agent[Deps, AgentDraft]:
    agent = Agent(
        model or DEFAULT_MODEL,
        output_type=AgentDraft,
        deps_type=Deps,
        instructions=SYSTEM_PROMPT,
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


def run_turn(
    question: str,
    nri: dict[str, Any],
    history: list[Any] | None = None,
    agent: Agent[Deps, AgentDraft] | None = None,
) -> TurnResult:
    """One conversational turn. `history` carries follow-up context."""
    agent = agent or build_agent()
    deps = build_deps(nri)
    result = agent.run_sync(question, deps=deps, message_history=history or None)
    return TurnResult(response=assemble(result.output, deps), messages=list(result.all_messages()))
