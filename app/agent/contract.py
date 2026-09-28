"""The LLM contract: what the model may hand back, and what a caller receives.

Two type families on opposite sides of a trust boundary, the same shape the
rest of this project uses for external data:

  `AgentDraft`   a Pydantic model, and exactly the JSON schema the model is
                 constrained to fill. Untrusted input. Malformed output fails
                 here as a typed validation error, not three layers later.
  `AgentResponse` what the API returns. Assembled by `assemble.py` from the
                 draft PLUS the deterministic engine's output.

THE DRAFT CARRIES NO NUMBERS THE ENGINE OWNS.

There is no `risk_score` field on `AgentDraft`, and that absence is the
assignment's "deterministic scoring, not only LLM output" requirement made
structural. A validator that compares a model-stated score against the real
one can only catch a disagreement after the model has already produced it, and
then has to decide whether to repair, reject or overwrite. A schema with no
score field cannot represent the disagreement at all: the model names WHICH
hubs and WHICH hazard, and the scores are joined in afterwards from the engine.
There is no code path by which a generated number reaches a user.

The model is also not asked to rank. `intent` tells the application what to
compute; the ordering comes from `engine.rank_hubs`.

WHAT vs WHY IS SPLIT BY THE SCHEMA, NOT BY ASKING. `answer` may only restate
what the tools returned. Any causal or explanatory claim goes in
`interpretation`, which is presented to the reader as unverified. A prompt
instruction to separate them is a request; a second required field is a
structure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Intent = Literal[
    "rank",
    "compare",
    # The cross-hazard investment shortlist. It needs its own value because
    # `rank` is per-hazard: the model has to name a hazard for it, and the
    # question this system exists to answer -- which handful of hubs should get
    # next year's resilience budget -- names none.
    "portfolio",
    "explain",
    "measure",
    # A question about the model itself rather than about a hub. It needs its
    # own value because `explain` requires hub_ids: without it, "what
    # assumptions go into the score?" had to be attached to an arbitrary hub,
    # and the answer then described that hub's data quirks instead of the
    # method.
    "methodology",
    "clarify",
    "out_of_scope",
]
Hazard = Literal["winter", "hurricane", "flood"]


class AgentDraft(BaseModel):
    """The model's structured output.

    Field descriptions are serialised into the JSON schema the model sees, so
    they are instructions rather than documentation.
    """

    interpretation_of_question: str = Field(
        min_length=1,
        description=(
            "One or two sentences restating how you understood the question, "
            "naming the hubs and hazard you took it to be about."
        ),
    )
    intent: Intent = Field(
        description=(
            "What kind of answer this question needs. 'rank' to order hubs by "
            "risk for ONE named hazard, 'portfolio' for an investment or "
            "budget-priority question across all hazards ('which hubs should we "
            "invest in', 'where should the resilience budget go', 'our biggest "
            "exposures') -- name no hazard for it, "
            "'compare' for two or more named hubs, 'explain' for why one "
            "hub scores as it does, 'measure' for a direct statistic such as a "
            "count or percentage of days, 'methodology' for a question about "
            "how the score itself works -- its weights, thresholds, bands or "
            "assumptions, 'clarify' when the question is too ambiguous to "
            "answer, 'out_of_scope' when this system cannot cover what was "
            "asked: an unknown hub, an unsupported hazard, an OUTCOME it does "
            "not measure (delays, closures, cost, tonnage), a PERIOD beyond "
            "its data, or a PRECISION its data cannot support."
        )
    )
    hub_ids: list[str] = Field(
        default_factory=list,
        description=(
            "The hub ids this question is about, exactly as the hub tools "
            "return them. Empty for 'clarify', 'out_of_scope', 'methodology' "
            "and 'portfolio', and empty for a 'rank' over a whole region (name "
            "the region instead)."
        ),
    )
    region: str | None = Field(
        default=None,
        description=(
            "For a 'rank' or 'portfolio' restricted to one region: Midwest, "
            "Northeast, South or West. Null for specific hubs or the whole "
            "network."
        ),
    )
    hazards: list[Hazard] = Field(
        default_factory=list,
        description=(
            "Every hazard this question is about, from: winter, hurricane, "
            "flood. Usually one. Name SEVERAL when the question asks about "
            "several ('hurricane and flood exposure') or is open-ended about "
            "overall weather risk ('why is this hub risky?'), in which case "
            "name all three. Empty for 'clarify', 'out_of_scope', "
            "'methodology', 'portfolio' (which always covers all three), or a "
            "'measure' that is not hazard-specific. For a "
            "'methodology' question about one hazard's weights, name that "
            "hazard."
        ),
    )
    answer: str = Field(
        min_length=1,
        description=(
            "The answer, in plain prose, stating ONLY what the tool results "
            "show. Every number you write must have appeared in a tool result. "
            "Never state a risk score you were not given. Do not explain WHY "
            "here -- that belongs in interpretation."
        ),
    )
    interpretation: list[str] = Field(
        default_factory=list,
        description=(
            "Any causal or explanatory claim -- anything of the form 'because', "
            "'driven by', 'as a result of'. These go beyond what the data "
            "establishes and are shown to the user as unverified. Empty list "
            "when you have none; padding it is worse than leaving it empty."
        ),
    )
    caveats: list[str] = Field(
        default_factory=list,
        description=(
            "What would make this answer wrong or overstated: a limit of the "
            "data, a confound, an alternative reading."
        ),
    )
    clarification_question: str | None = Field(
        default=None,
        description=(
            "When intent is 'clarify', the single most useful question to ask "
            "back. Null otherwise."
        ),
    )
    out_of_scope_reason: str | None = Field(
        default=None,
        description=(
            "When intent is 'out_of_scope', what was asked for that this "
            "system does not cover, and which limit it runs into: an unknown "
            "hub, an unsupported hazard, an outcome this system does not "
            "measure, a period beyond its data, or a precision its data "
            "cannot support. Null otherwise."
        ),
    )

    @model_validator(mode="after")
    def _intent_matches_its_payload(self) -> AgentDraft:
        """A draft must be internally coherent. A model that is unsure has to
        say so in the structured field rather than quietly picking an
        interpretation and answering it."""
        if self.intent == "clarify" and not (self.clarification_question or "").strip():
            raise ValueError("intent is 'clarify' but clarification_question is empty")
        if self.intent == "out_of_scope" and not (self.out_of_scope_reason or "").strip():
            raise ValueError("intent is 'out_of_scope' but out_of_scope_reason is empty")
        if self.intent in ("compare", "explain") and not self.hub_ids:
            raise ValueError(f"intent is {self.intent!r} but no hub_ids were given")
        if self.intent == "compare" and len(self.hub_ids) < 2:
            raise ValueError("intent is 'compare' but fewer than two hubs were named")
        if self.intent in ("rank", "compare", "explain") and not self.hazards:
            raise ValueError(f"intent is {self.intent!r} but no hazard was named")
        return self


# ------------------------------------------------------- the trusted side --


@dataclass(frozen=True)
class HubAssessment:
    """One hub's deterministic result. Built from `engine.HazardScore`, never
    from model output."""

    hub_id: str
    hub: str
    region: str
    hazard: str
    risk_score: float
    risk_band: str
    rank: int | None
    main_drivers: tuple[str, ...]
    component_breakdown: tuple[str, ...]
    evidence: tuple[str, ...]
    active_alerts: tuple[str, ...]
    # Computed by the engine so the agent never subtracts two scores itself.
    # Carried into the response so a quoted gap is auditable against it.
    #
    # BOTH ARE None ON A PORTFOLIO ROW, and that is deliberate. The rows there
    # span hazards, so the hub above may be scored from a different set of
    # components -- handing the model a pre-computed difference would MANUFACTURE
    # the cross-hazard comparison the portfolio exists to avoid making. Absent is
    # the honest value; a 0.0 would read as "level with the leader".
    gap_to_leader: float | None = None
    gap_to_next: float | None = None
    # The investment reading. Defaulted because only a portfolio response
    # carries a tier -- a single-hazard ranking is a risk answer, and labelling
    # its rows with an investment tier would invite the two to be read as one.
    #
    # `structural_score` is None when nothing structural could be measured,
    # which is never the same as zero. See engine.HazardScore.structural_score.
    structural_score: float | None = None
    investability: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "hub_id": self.hub_id,
            "hub": self.hub,
            "region": self.region,
            "hazard": self.hazard,
            "risk_score": self.risk_score,
            "risk_band": self.risk_band,
            "rank": self.rank,
            "gap_to_leader": self.gap_to_leader,
            "gap_to_next": self.gap_to_next,
            "main_drivers": list(self.main_drivers),
            "component_breakdown": list(self.component_breakdown),
            "evidence": list(self.evidence),
            "active_alerts": list(self.active_alerts),
            # A field missing from here is invisible to the API, the UI and the
            # evals, however correct the dataclass is.
            "structural_score": self.structural_score,
            "investability": self.investability,
        }


@dataclass(frozen=True)
class AgentResponse:
    """What `/chat` returns. The scores in `assessments` come from the engine."""

    answer: str
    intent: str
    interpretation_of_question: str
    assessments: tuple[HubAssessment, ...]
    interpretation: tuple[str, ...]
    assumptions: tuple[str, ...]
    uncertainty: tuple[str, ...]
    sources: tuple[str, ...]
    clarification_question: str | None = None
    out_of_scope_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer": self.answer,
            "intent": self.intent,
            "interpretation_of_question": self.interpretation_of_question,
            "assessments": [a.as_dict() for a in self.assessments],
            "interpretation": list(self.interpretation),
            "assumptions": list(self.assumptions),
            "uncertainty": list(self.uncertainty),
            "sources": list(self.sources),
            "clarification_question": self.clarification_question,
            "out_of_scope_reason": self.out_of_scope_reason,
        }
