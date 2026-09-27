"""The evaluation set: what each case asks, and what a correct handling must do.

Data, not logic. A reviewer should be able to read this file top to bottom and
disagree with a specific expectation without reading any other module.

NO EXPECTATION HERE IS A HARDCODED SCORE. Every numeric expectation is either
a reference computed at run time by the same deterministic engine the agent
uses, or a tolerance around one. That is deliberate: a case that asserts
"Milwaukee scores 66.2" starts failing the day the weights change or a new
NRI release lands, and would then be measuring staleness rather than
correctness. What IS hand-written is the semantics -- which hub, which hazard,
which intent, which refusal -- because that is the part that needs a human.

Ten cases, covering every archetype in the assignment plus the failure modes
that matter: an unknown hub, an out-of-scope hazard, an ambiguous reference,
and two conversational follow-ups.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Intent = Literal["rank", "compare", "explain", "measure", "clarify", "out_of_scope"]


@dataclass(frozen=True)
class Case:
    case_id: str
    category: str
    question: str

    # ---- what the agent must decide -----------------------------------
    expect_intent: tuple[Intent, ...]
    expect_hazards: tuple[str, ...] = ()
    # Hub ids that MUST appear in the assessments.
    expect_hubs: tuple[str, ...] = ()
    # Hub ids that must appear, in this relative order, in the ranking.
    expect_order: tuple[str, ...] = ()

    # ---- what the agent must measure ----------------------------------
    # (hub_id, metric, year, expected_percent) -- the percentage is computed
    # from the snapshot at run time, never written here.
    expect_measurement: tuple[str, str, str] | None = None
    # Absolute tolerance in percentage points for the measured figure.
    tolerance_pp: float = 0.2

    # ---- what the answer must contain ---------------------------------
    # Lowercase substrings, any of which satisfies the requirement.
    expect_any_phrase: tuple[str, ...] = ()
    # Lowercase substrings that must NOT appear.
    forbid_phrases: tuple[str, ...] = ()
    expect_clarification: bool = False
    expect_out_of_scope: bool = False

    # ---- conversation -------------------------------------------------
    # Case ids whose turns run first, in order, sharing one conversation.
    follows: tuple[str, ...] = ()

    notes: str = ""


CASES: tuple[Case, ...] = (
    # ------------------------------------------------- the four headline asks --
    Case(
        case_id="rank_midwest_winter",
        category="ranking",
        question="Which hubs in the Midwest are most exposed to winter disruption?",
        expect_intent=("rank",),
        expect_hazards=("winter",),
        # Ten Midwest hubs must all be scored: the answer to "which hubs" is
        # the whole ordering, not a top-three the model chose to mention.
        expect_hubs=(
            "chicago-il", "minneapolis-mn", "detroit-mi", "milwaukee-wi",
            "columbus-oh", "omaha-ne", "indianapolis-in", "kansas-city-mo",
            "des-moines-ia", "st-louis-mo",
        ),
        notes=(
            "The ordering is asserted against the engine's own ranking rather "
            "than a hand-written list, so changing the weights changes the "
            "expectation automatically."
        ),
    ),
    Case(
        case_id="compare_miami_houston_hurricane",
        category="comparison",
        question="Compare Miami and Houston in terms of hurricane exposure.",
        expect_intent=("compare",),
        expect_hazards=("hurricane",),
        expect_hubs=("miami-fl", "houston-tx"),
        notes=(
            "Both sit at FEMA's maximum hurricane index, so a near-tie is the "
            "CORRECT answer. The check must not demand a winner -- inventing a "
            "difference would be exactly the failure this system exists to "
            "prevent."
        ),
    ),
    Case(
        case_id="compare_miami_houston_flood",
        category="comparison",
        question="Compare Miami and Houston for flood exposure.",
        expect_intent=("compare",),
        expect_hazards=("flood",),
        expect_hubs=("miami-fl", "houston-tx"),
    ),
    Case(
        case_id="compare_miami_houston_both_hazards",
        category="comparison",
        question="Compare Miami and Houston in terms of hurricane and flood exposure.",
        expect_intent=("compare",),
        # BOTH hazards, in one question. This is the assignment's example
        # verbatim, and it is the exact shape that was silently broken: the
        # draft schema carried ONE hazard, so a question naming two assembled
        # assessments for one and discarded the other -- the answer still read
        # fine, with half its numbers unverifiable. Splitting this into two
        # single-hazard cases (as the two above do) does not test it.
        expect_hazards=("hurricane", "flood"),
        expect_hubs=("miami-fl", "houston-tx"),
        expect_any_phrase=("hurricane",),
    ),
    Case(
        case_id="measure_denver_snow",
        category="measurement",
        question="What percentage of days in Denver last year had snowfall?",
        expect_intent=("measure",),
        # "last year" must resolve against today's clock, not the model's
        # training data. The harness computes the year at run time.
        expect_measurement=("denver-co", "any_snowfall", "__last_year__"),
        expect_any_phrase=("%",),
        notes=(
            "The first live run answered for 2024 because nothing told the "
            "model today's date. This case exists to keep that fixed."
        ),
    ),
    Case(
        case_id="explain_dallas",
        category="explanation",
        question="Why is the Dallas hub's weather disruption risk high?",
        expect_intent=("explain", "rank", "compare"),
        expect_hubs=("dallas-tx",),
        expect_any_phrase=("baseline", "fema", "flood", "historical"),
        notes="Must cite components, not offer a meteorological story.",
    ),
    # ------------------------------------------------------ follow-up context --
    Case(
        case_id="followup_flood_only",
        category="follow-up",
        question="What about flooding only?",
        expect_intent=("compare", "rank"),
        expect_hazards=("flood",),
        expect_hubs=("miami-fl", "houston-tx"),
        follows=("compare_miami_houston_hurricane",),
        notes=(
            "Carries the hubs forward and switches the hazard. A fresh-context "
            "model would have no idea which hubs are meant."
        ),
    ),
    Case(
        case_id="followup_top_component",
        category="follow-up",
        question="Which component contributed most?",
        expect_intent=("explain", "compare", "rank"),
        expect_any_phrase=("baseline", "historical", "current"),
        follows=("compare_miami_houston_flood",),
        notes=(
            "Answerable as a lookup: the engine exposes top_driver. A model "
            "judging 'which mattered most' from prose would be guessing."
        ),
    ),
    # ----------------------------------------------------------- refusal paths --
    Case(
        case_id="unknown_hub",
        category="refusal",
        question="How exposed is our Reykjavik hub to winter weather?",
        expect_intent=("out_of_scope", "clarify"),
        expect_out_of_scope=True,
        # The model knows Reykjavik's weather. It must not use that knowledge.
        forbid_phrases=("risk score of", "scores 7", "scores 8", "scores 9"),
        notes=(
            "The dangerous failure is a plausible answer about a city the "
            "system has no data for."
        ),
    ),
    Case(
        case_id="out_of_scope_hazard",
        category="refusal",
        question="Which hubs face the worst wildfire risk?",
        expect_intent=("out_of_scope", "clarify"),
        expect_out_of_scope=True,
        expect_any_phrase=("wildfire", "not", "winter", "hurricane", "flood"),
        notes=(
            "FEMA NRI carries a wildfire index, so the data is arguably there "
            "-- but no weights, threshold or alert mapping were designed for "
            "it. Answering anyway would be inventing a scoring rule at "
            "runtime, which the core principle forbids."
        ),
    ),
    Case(
        case_id="ambiguous_portland",
        category="clarification",
        question="How risky is Portland?",
        expect_intent=("clarify",),
        expect_clarification=True,
        expect_any_phrase=("portland",),
        notes=(
            "Two hubs are called Portland (ME and OR) and no hazard was "
            "named. Two independent reasons to ask rather than guess."
        ),
    ),
)


CASES_BY_ID = {case.case_id: case for case in CASES}


def resolution_order() -> tuple[Case, ...]:
    """Cases ordered so every prerequisite runs before the case that needs it."""
    ordered: list[Case] = []
    seen: set[str] = set()

    def visit(case: Case) -> None:
        if case.case_id in seen:
            return
        for prerequisite in case.follows:
            visit(CASES_BY_ID[prerequisite])
        seen.add(case.case_id)
        ordered.append(case)

    for case in CASES:
        visit(case)
    return tuple(ordered)
