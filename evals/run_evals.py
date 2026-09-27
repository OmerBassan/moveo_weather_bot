"""Run the evaluation set through the REAL pipeline and score what comes back.

    python -m evals.run_evals                  # all cases
    python -m evals.run_evals --model anthropic:claude-haiku-4-5-20251001
    python -m evals.run_evals --case measure_denver_snow
    python -m evals.run_evals --json evals/results.json

There is no second agent here. Every case goes through `runner.run_turn` --
the same function `/chat` calls, with the same tools, the same engine and the
same live alerts. The harness only observes.

WHY THE ENGINE IS RE-RUN INDEPENDENTLY. For each case the harness calls the
scoring engine itself, outside the agent path, and compares. That is the check
that proves the central claim: if a score in the response ever disagrees with
a freshly computed one, a model-supplied number reached the user. Asserting it
per case per run is the difference between a design that intends to be safe
and one that is demonstrably safe.

AN EVALUATION RESULT IS NOT A TEST FAILURE. Nothing here raises on a wrong
answer; the case is recorded with its failures and the run continues. Only the
harness's own breakage is an error. The exit code reflects the pass rate so CI
can still gate on it.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import warnings
from datetime import date
from pathlib import Path
from typing import Any

from evals import checks
from evals.cases import Case, resolution_order
from evals.report import render_report

warnings.filterwarnings("ignore")
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
logging.getLogger("httpx").setLevel(logging.WARNING)

from app.agent.runner import build_agent, build_deps, run_turn  # noqa: E402
from app.config import load_config  # noqa: E402
from app.hubs import load_hubs  # noqa: E402
from app.scoring import climatology  # noqa: E402
from app.scoring.engine import rank_hubs  # noqa: E402
from app.agent.tools import MEASUREMENT_METRICS  # noqa: E402


def _independent_rankings(
    response: dict[str, Any], deps: Any
) -> tuple[dict[tuple[str, str], float], dict[str, list[str]]]:
    """Re-score everything the response claims, from the engine, per hazard.

    This is the load-bearing check. It calls the engine directly -- outside
    the agent path, on the same alerts -- and any disagreement means a
    model-supplied number reached the user.
    """
    registry = load_hubs()
    by_hazard: dict[str, list[str]] = {}
    for assessment in response.get("assessments", []):
        by_hazard.setdefault(assessment.get("hazard", ""), []).append(
            assessment["hub_id"]
        )

    scores: dict[tuple[str, str], float] = {}
    orders: dict[str, list[str]] = {}
    for hazard, hub_ids in by_hazard.items():
        if hazard not in ("winter", "hurricane", "flood"):
            continue
        # dict.fromkeys preserves order while removing duplicates.
        hubs = tuple(
            h
            for h in (registry.by_id(i) for i in dict.fromkeys(hub_ids))
            if h is not None
        )
        ranked = rank_hubs(hubs, hazard, deps.nri, deps.engine_alerts())
        orders[hazard] = [r.hub_id for r in ranked]
        for result in ranked:
            scores[(hazard, result.hub_id)] = result.score
    return scores, orders


def _expected_measurement(case: Case) -> tuple[float, set[float]]:
    """Compute the reference figure from the snapshot, at run time."""
    hub_id, metric, year = case.expect_measurement  # type: ignore[misc]
    if year == "__last_year__":
        year = str(date.today().year - 1)
    variable, threshold, strict, _ = MEASUREMENT_METRICS[metric]
    count = climatology.count_days(hub_id, variable, threshold, year=year, strict=strict)
    allowed = {
        count.percent_of_observed,
        float(count.days_matching),
        float(count.days_observed),
        round(count.percent_of_observed, 1),
    }
    # The companion figure is legitimately quotable too.
    other = "disruptive_snowfall" if metric == "any_snowfall" else "any_snowfall"
    if other in MEASUREMENT_METRICS:
        o_var, o_threshold, o_strict, _ = MEASUREMENT_METRICS[other]
        o_count = climatology.count_days(
            hub_id, o_var, o_threshold, year=year, strict=o_strict
        )
        allowed.update(
            {
                o_count.percent_of_observed,
                round(o_count.percent_of_observed, 1),
                float(o_count.days_matching),
                float(o_count.days_observed),
            }
        )
    return count.percent_of_observed, allowed


def run_case(
    case: Case, agent: Any, nri: dict[str, Any], history: list[Any] | None
) -> tuple[checks.CaseResult, list[Any]]:
    result = checks.CaseResult(
        case_id=case.case_id, category=case.category, question=case.question
    )
    started = time.monotonic()
    try:
        turn = run_turn(case.question, nri, history=history, agent=agent)
    except Exception as exc:  # noqa: BLE001 - a harness must survive one bad case
        result.error = f"{type(exc).__name__}: {exc}"
        result.seconds = time.monotonic() - started
        return result, history or []

    result.seconds = time.monotonic() - started
    response = turn.response.as_dict()
    result.answer = response.get("answer", "")
    result.intent = response.get("intent", "")
    result.input_tokens = turn.input_tokens
    result.output_tokens = turn.output_tokens
    result.cached_tokens = turn.cached_tokens

    deps = build_deps(nri)
    extra_allowed: set[float] = set()

    result.checks.append(checks.check_intent(response, case.expect_intent))

    if case.expect_hubs:
        result.checks.append(checks.check_hubs_present(response, case.expect_hubs))
    if case.expect_hazards:
        result.checks.append(checks.check_hazard(response, case.expect_hazards))

    if response.get("assessments"):
        scores, orders = _independent_rankings(response, deps)
        result.checks.append(checks.check_score_integrity(response, scores))
        result.checks.append(checks.check_ordering(response, orders))
        result.checks.append(checks.check_disclosure(response))
        result.checks.append(checks.check_sources(response))

    if case.expect_measurement:
        expected, allowed = _expected_measurement(case)
        extra_allowed |= allowed
        result.checks.append(
            checks.check_measurement(response, expected, case.tolerance_pp)
        )

    result.checks.append(checks.check_groundedness(response, extra_allowed))

    result.checks.extend(
        checks.check_phrases(response, case.expect_any_phrase, case.forbid_phrases)
    )
    if case.expect_clarification:
        result.checks.append(
            checks.check_flag(
                "asked_for_clarification",
                bool(response.get("clarification_question")),
                True,
            )
        )
    if case.expect_out_of_scope:
        result.checks.append(
            checks.check_flag(
                "refused_as_out_of_scope",
                bool(response.get("out_of_scope_reason")),
                True,
            )
        )

    return result, turn.messages


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the weather-risk evaluation set.")
    parser.add_argument("--model", default=None, help="override the agent model")
    parser.add_argument("--case", default=None, help="run one case by id")
    parser.add_argument("--json", default=None, help="write raw results to this path")
    parser.add_argument(
        "--markdown", default="evals/results.md", help="write the report here"
    )
    args = parser.parse_args()

    config = load_config()
    nri = json.loads(config.nri_snapshot_path.read_text(encoding="utf-8"))
    agent = build_agent(args.model)
    model_name = args.model or "default (anthropic:claude-sonnet-5)"

    cases = resolution_order()
    if args.case:
        wanted = {args.case}
        # Keep prerequisites, or a follow-up would run with no context.
        cases = tuple(
            c for c in cases if c.case_id in wanted or c.case_id in _prereqs(args.case)
        )
        if not cases:
            print(f"no case named {args.case!r}", file=sys.stderr)
            return 2

    print(f"Running {len(cases)} case(s) against {model_name}\n")

    results: list[checks.CaseResult] = []
    conversations: dict[str, list[Any]] = {}

    for case in cases:
        # A follow-up shares the conversation of the case it follows.
        thread = case.follows[0] if case.follows else case.case_id
        history = conversations.get(thread)

        result, messages = run_case(case, agent, nri, history)
        conversations[thread] = messages
        results.append(result)

        mark = "PASS" if result.passed else ("ERROR" if result.error else "FAIL")
        print(f"  [{mark:5s}] {case.case_id:34s} {result.seconds:5.1f}s")
        for failure in result.failures:
            print(f"           - {failure.name}: {failure.detail}")
        if result.error:
            print(f"           ! {result.error}")

    report = render_report(results, model_name)
    Path(args.markdown).write_text(report, encoding="utf-8")
    print(f"\nReport written to {args.markdown}")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                [
                    {
                        "case_id": r.case_id,
                        "category": r.category,
                        "passed": r.passed,
                        "intent": r.intent,
                        "answer": r.answer,
                        "error": r.error,
                        "seconds": round(r.seconds, 2),
                        "tokens": {
                            "input": r.input_tokens,
                            "output": r.output_tokens,
                            "cached": r.cached_tokens,
                        },
                        "checks": [
                            {"name": c.name, "passed": c.passed, "detail": c.detail}
                            for c in r.checks
                        ],
                    }
                    for r in results
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"Raw results written to {args.json}")

    passed = sum(1 for r in results if r.passed)
    print(f"\n{passed}/{len(results)} cases passed")
    return 0 if passed == len(results) else 1


def _prereqs(case_id: str) -> set[str]:
    from evals.cases import CASES_BY_ID

    out: set[str] = set()
    stack = list(CASES_BY_ID[case_id].follows) if case_id in CASES_BY_ID else []
    while stack:
        current = stack.pop()
        out.add(current)
        stack.extend(CASES_BY_ID[current].follows)
    return out


if __name__ == "__main__":
    sys.exit(main())
