"""The deterministic half of the evaluation: pure functions, no model.

Five instruments, each narrow enough to be read and disputed line by line:

  SCORE INTEGRITY   every score in the response is recomputed independently
                    from the engine and must match exactly. This is the check
                    that proves the assignment's central claim.
  GROUNDEDNESS      every number in the prose must be traceable to a value the
                    system actually produced. Catches the model doing
                    arithmetic, or recalling a figure from training data.
  COVERAGE          the hubs and hazard the question was about are present.
  ORDERING          the ranking the answer states matches the engine's.
  BEHAVIOUR         intent, refusals, clarifications, forbidden phrasing.

NONE OF THESE IS AN LLM JUDGE, and none can be: a judge that grades an answer
is another model output whose correctness would itself need grading. The one
thing worth grading here -- is the number right -- is exactly the thing a
deterministic check does better than any judge.

KNOWN LIMITS, stated so a reviewer does not have to find them. Number
extraction reads digits only, so a figure written as a word ("forty-four") is
invisible to the groundedness check -- it never flags a correct number written
that way, it just cannot see it. Matching accepts a value rounded to the
precision the prose shows, because that is how people correctly restate a
returned number -- which also means a small integer that happens to round to a
real component value cannot be told apart from an invented one ("3 closures"
against a measured 3.2 days/year). This check bounds how wrong a figure can be;
it does not prove the figure was used for the thing it measures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Years, and small integers that are almost always prose rather than data
# ("the top 3", "both hubs"). Excluded to keep the groundedness check focused
# on claims about measurements.
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])(?P<num>\d{1,3}(?:,\d{3})*(?:\.\d+)?)(?![A-Za-z0-9_])")
_LIST_MARKER = re.compile(r"(?m)^\s*\d{1,2}[.)]\s+")
# ISO dates inside evidence lines ("2021-01-01 to 2026-09-20") bound a period;
# they are not measured values. Mining them into the allowed set quietly
# whitelisted 1, 9, 20 and 21 for every answer -- most of the small-integer
# range this check was just tightened to cover.
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

# WHAT REVOKES THE SMALL-INTEGER EXEMPTION.
#
# Exempting every integer up to 40 as "a count, a rank, a hub tally" left the
# check blind in exactly the range a fabricated operational figure occupies:
# "expect 8 delays", "3 closure days", "12 hours of downtime" all passed
# silently, and those are the numbers this system has no data to produce at
# all. A unit or an outcome word after the digits is what separates a claim
# about measured quantity from ordinary prose, so a small integer keeps its
# exemption only when nothing of the kind follows it.
_MEASURED_UNIT = re.compile(
    r"^[\s-]*(?:%|(?:percent|percentage|pp|day|days|night|nights|hour|hours|week|weeks"
    r"|month|months|inch|inches|mm|cm|mph|kt|knots|degree|degrees|point|points|pts"
    r"|delay|delays|closure|closures|shutdown|shutdowns|disruption|disruptions"
    r"|shipment|shipments|truck|trucks|load|loads|incident|incidents|outage|outages"
    r"|dollar|dollars|usd)\b)",
    re.IGNORECASE,
)
# A RELATIVE WINDOW IS STILL PROSE. "over the next 3 days" and "in the last 5
# years" name a period, they do not assert a measurement, so the unit word
# after them must not turn them into one.
_TIME_WINDOW = re.compile(
    r"(?:next|last|past|previous|coming|recent|within|over the|for the)\s*$",
    re.IGNORECASE,
)


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class CaseResult:
    case_id: str
    category: str
    question: str
    checks: list[CheckResult] = field(default_factory=list)
    answer: str = ""
    intent: str = ""
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    seconds: float = 0.0

    @property
    def passed(self) -> bool:
        return self.error is None and all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


def extract_numbers(text: str, *, drop_dates: bool = False) -> list[float]:
    """Every digit-written number in `text`, as floats.

    `drop_dates` removes ISO dates first, for callers building the set of values
    an answer is allowed to quote: a date bounds a period, it is not a figure
    the system measured.
    """
    cleaned = _LIST_MARKER.sub(" ", text)
    if drop_dates:
        cleaned = _ISO_DATE.sub(" ", cleaned)
    values: list[float] = []
    for match in _NUMBER.finditer(cleaned):
        try:
            values.append(float(match.group("num").replace(",", "")))
        except ValueError:
            continue
    return values


def _ungrounded_in(text: str, allowed: set[float]) -> list[float]:
    """Numbers in `text` that trace to nothing the system produced."""
    cleaned = _LIST_MARKER.sub(" ", text)
    ungrounded: list[float] = []
    for match in _NUMBER.finditer(cleaned):
        try:
            value = float(match.group("num").replace(",", ""))
        except ValueError:
            continue
        if 1900 <= value <= 2100 and value == int(value):
            continue  # a year
        if any(_matches_at_shown_precision(value, candidate) for candidate in allowed):
            continue
        if value <= 40 and value == int(value):
            after = cleaned[match.end() : match.end() + 24]
            before = cleaned[max(0, match.start() - 24) : match.start()]
            if not _MEASURED_UNIT.match(after):
                continue  # a tally, a rank, "both of the 2" -- prose
            if _TIME_WINDOW.search(before):
                continue  # "over the next 3 days" names a window, not a quantity
        ungrounded.append(value)
    return ungrounded


def _matches_at_shown_precision(claimed: float, actual: float) -> bool:
    """Does `claimed` restate `actual` correctly at the precision written?

    "73.1" for 73.14 is correct. "73" for 73.1 is correct. "74" for 73.1 is not.
    """
    if claimed == actual:
        return True
    text = f"{claimed:g}"
    decimals = len(text.split(".")[1]) if "." in text else 0
    return round(actual, decimals) == round(claimed, decimals)


def check_score_integrity(
    response: dict[str, Any], recomputed: dict[tuple[str, str], float]
) -> CheckResult:
    """Every score in the response must equal an independently recomputed one.

    `recomputed` is produced by calling the engine directly, outside the agent
    path. If these ever disagree, a model-supplied number reached the user --
    which is the one failure this architecture is built to make impossible.
    """
    mismatches: list[str] = []
    for assessment in response.get("assessments", []):
        key = (assessment.get("hazard"), assessment.get("hub_id"))
        stated = assessment.get("risk_score")
        expected = recomputed.get(key)
        if expected is None:
            mismatches.append(f"{key}: not in the recomputed set")
        elif abs(float(stated) - expected) > 1e-9:
            mismatches.append(f"{key}: response says {stated}, engine says {expected}")

    return CheckResult(
        name="score_integrity",
        passed=not mismatches,
        detail="; ".join(mismatches) or f"{len(response.get('assessments', []))} score(s) verified",
    )


def check_groundedness(response: dict[str, Any], extra_allowed: set[float]) -> CheckResult:
    """Every number in the prose must trace to a value the system produced.

    Allowed values are the scores, ranks, gaps and component figures the
    engine emitted, plus whatever the caller adds (measured percentages, day
    counts). Years are exempt, and so is a small integer with no unit after it:
    "the top 3 hubs" and "in 2025" are prose, not claims about a measurement.

    BOTH PROSE FIELDS ARE CHECKED. `interpretation` was exempt, which is the
    wrong way round: it is the field the prompt pushes every explanatory
    sentence into, so it is where a number is most likely to arrive carrying an
    argument. Being labelled unverified to the reader is not a licence to put
    an unsourced figure there.
    """
    allowed: set[float] = set(extra_allowed)
    for assessment in response.get("assessments", []):
        allowed.add(float(assessment.get("risk_score", 0)))
        if (rank := assessment.get("rank")) is not None:
            allowed.add(float(rank))
        # The engine-computed gaps, so a quoted "4.6 points behind" is
        # grounded. These are why the agent never has to subtract two scores.
        for key in ("gap_to_leader", "gap_to_next"):
            if (gap := assessment.get(key)) is not None:
                allowed.add(float(gap))
        # The structural score, for the same reason: a portfolio answer quotes
        # it constantly, and it appears in no component_breakdown line because
        # it is derived from the components rather than being one of them.
        if (structural := assessment.get("structural_score")) is not None:
            allowed.add(float(structural))
        for line in assessment.get("component_breakdown", ()):
            allowed.update(extract_numbers(line, drop_dates=True))
        for line in assessment.get("evidence", ()):
            allowed.update(extract_numbers(line, drop_dates=True))

    findings: list[str] = []
    if answer_bad := _ungrounded_in(response.get("answer", ""), allowed):
        findings.append(f"answer: {answer_bad}")
    interpretation = " ".join(response.get("interpretation", ()) or ())
    if interpretation_bad := _ungrounded_in(interpretation, allowed):
        findings.append(f"interpretation: {interpretation_bad}")

    return CheckResult(
        name="groundedness",
        passed=not findings,
        detail=(
            f"numbers with no source -- {'; '.join(findings)}"
            if findings
            else "every figure traces to a tool result"
        ),
    )


def check_intent(response: dict[str, Any], expected: tuple[str, ...]) -> CheckResult:
    actual = response.get("intent", "")
    return CheckResult(
        name="intent",
        passed=actual in expected,
        detail=f"got {actual!r}, expected one of {expected}",
    )


def check_hubs_present(response: dict[str, Any], expected: tuple[str, ...]) -> CheckResult:
    present = {a.get("hub_id") for a in response.get("assessments", [])}
    missing = [hub for hub in expected if hub not in present]
    return CheckResult(
        name="hub_coverage",
        passed=not missing,
        detail=f"missing: {missing}" if missing else f"{len(expected)} hub(s) assessed",
    )


def check_hazard(response: dict[str, Any], expected: tuple[str, ...]) -> CheckResult:
    """The expected hazards must all be assessed.

    Containment rather than equality: a question like "why is Dallas risky?"
    is correctly answered across all three hazards, and scoring more than was
    strictly asked is thoroughness, not error. What must never happen is the
    ANSWER discussing a hazard that has no assessment behind it -- and the
    groundedness check catches that, because those numbers would have no source.
    """
    if not expected:
        return CheckResult(name="hazard", passed=True, detail="not constrained")
    assessed = {a.get("hazard") for a in response.get("assessments", [])}
    missing = [h for h in expected if h not in assessed]
    return CheckResult(
        name="hazard",
        passed=not missing,
        detail=f"missing {missing}" if missing else f"assessed {sorted(assessed)}",
    )


def check_ordering(
    response: dict[str, Any], engine_order: dict[str, list[str]]
) -> CheckResult:
    """The response's ranking must match the engine's, per hazard."""
    by_hazard: dict[str, list[tuple[int, str]]] = {}
    for assessment in response.get("assessments", []):
        by_hazard.setdefault(assessment.get("hazard", ""), []).append(
            (assessment.get("rank") or 0, assessment["hub_id"])
        )

    problems: list[str] = []
    for hazard, rows in by_hazard.items():
        stated = [hub for _, hub in sorted(rows)]
        expected = engine_order.get(hazard, [])
        if stated != expected:
            problems.append(f"{hazard}: response {stated[:4]} vs engine {expected[:4]}")

    return CheckResult(
        name="ranking_matches_engine",
        passed=not problems,
        detail="; ".join(problems) or f"{len(by_hazard)} hazard ranking(s) match",
    )


def check_portfolio_ordering(
    response: dict[str, Any], expected_order: list[str]
) -> CheckResult:
    """A portfolio response is ONE ordering that spans hazards, so it cannot be
    verified the way a ranking is.

    `check_ordering` groups by hazard and compares each group against a
    descending-score ranking. A portfolio is ordered by tier first, so a
    lower-scoring Invest hub legitimately precedes a higher-scoring Low one --
    which is the entire feature, and would read as a failure there. Verified
    against `engine.rank_portfolio` instead, called independently of the agent
    on the same weather, so the one new ordering in the system is not also the
    only unverified one.
    """
    stated = [
        assessment["hub_id"]
        for assessment in sorted(
            response.get("assessments", []),
            key=lambda a: a.get("rank") or 0,
        )
    ]
    matches = stated == expected_order
    return CheckResult(
        name="portfolio_matches_engine",
        passed=matches,
        detail=(
            f"{len(stated)} hubs in the engine's order"
            if matches
            else f"response {stated[:4]} vs engine {expected_order[:4]}"
        ),
    )


def check_tiers_are_grouped(response: dict[str, Any]) -> CheckResult:
    """Tiers must appear in blocks, never interleaved.

    A tier that reappears further down the list is indistinguishable, to a
    reader, from an ordering bug -- and it would mean the response reordered
    what the engine returned.
    """
    order = ["Invest", "Watch", "Low"]
    seen: list[str] = []
    for assessment in sorted(
        response.get("assessments", []), key=lambda a: a.get("rank") or 0
    ):
        tier = assessment.get("investability") or "not_tierable"
        if not seen or seen[-1] != tier:
            seen.append(tier)

    duplicated = [tier for tier in set(seen) if seen.count(tier) > 1]
    ranked = [order.index(t) for t in seen if t in order]
    problems: list[str] = []
    if duplicated:
        problems.append(f"tier(s) {duplicated} appear in more than one block")
    if ranked != sorted(ranked):
        problems.append(f"tiers out of order: {seen}")

    return CheckResult(
        name="tiers_grouped",
        passed=not problems,
        detail="; ".join(problems) or f"tier blocks in order: {seen}",
    )


def check_measurement(
    response: dict[str, Any], expected_percent: float, tolerance: float
) -> CheckResult:
    """The answer must state the measured percentage, within tolerance."""
    numbers = extract_numbers(response.get("answer", ""))
    hit = [n for n in numbers if abs(n - expected_percent) <= tolerance]
    return CheckResult(
        name="measurement_accuracy",
        passed=bool(hit),
        detail=(
            f"stated {hit[0]} against expected {expected_percent:.1f} (+/-{tolerance})"
            if hit
            else f"expected ~{expected_percent:.1f}% in the answer; found {numbers}"
        ),
    )


def check_phrases(
    response: dict[str, Any], any_of: tuple[str, ...], forbidden: tuple[str, ...]
) -> list[CheckResult]:
    haystack = " ".join(
        [
            response.get("answer", ""),
            response.get("out_of_scope_reason") or "",
            response.get("clarification_question") or "",
        ]
    ).lower()

    results: list[CheckResult] = []
    if any_of:
        found = [phrase for phrase in any_of if phrase in haystack]
        results.append(
            CheckResult(
                name="expected_phrasing",
                passed=bool(found),
                detail=f"found {found}" if found else f"none of {any_of} appeared",
            )
        )
    if forbidden:
        breaches = [phrase for phrase in forbidden if phrase in haystack]
        results.append(
            CheckResult(
                name="no_forbidden_phrasing",
                passed=not breaches,
                detail=f"said {breaches}" if breaches else "clean",
            )
        )
    return results


def check_disclosure(response: dict[str, Any]) -> CheckResult:
    """An answer carrying scores must carry the assumptions behind them.

    The engine always attaches at least the anchor assumption to a scored
    hazard, so an empty list means the agent dropped them -- which is how a
    prototype weighting quietly becomes an unqualified fact.
    """
    if not response.get("assessments"):
        return CheckResult(name="disclosure", passed=True, detail="no scores to qualify")
    has = bool(response.get("assumptions")) or bool(response.get("uncertainty"))
    return CheckResult(
        name="disclosure",
        passed=has,
        detail=(
            f"{len(response.get('assumptions', []))} assumption(s), "
            f"{len(response.get('uncertainty', []))} uncertainty note(s)"
            if has
            else "scores reported with no assumptions or uncertainty"
        ),
    )


def check_sources(response: dict[str, Any]) -> CheckResult:
    if not response.get("assessments"):
        return CheckResult(name="sources", passed=True, detail="no data cited")
    sources = response.get("sources", [])
    return CheckResult(
        name="sources",
        passed=bool(sources),
        detail=", ".join(sources) if sources else "no sources reported",
    )


def check_flag(name: str, actual: bool, expected: bool) -> CheckResult:
    return CheckResult(
        name=name,
        passed=actual == expected,
        detail=f"{'present' if actual else 'absent'}, expected {'present' if expected else 'absent'}",
    )
