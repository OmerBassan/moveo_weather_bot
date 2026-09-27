"""Turn evaluation results into a report that can go straight into the submission."""

from __future__ import annotations

from datetime import datetime, timezone

from evals.checks import CaseResult

CHECK_DESCRIPTIONS = {
    "score_integrity": "response scores equal an independently recomputed engine score",
    "groundedness": "every number in the prose traces to a tool result",
    "ranking_matches_engine": "the stated order matches the engine's order",
    "intent": "the question was routed to the right kind of answer",
    "hub_coverage": "the hubs the question was about were all assessed",
    "hazard": "the right hazard was scored",
    "measurement_accuracy": "the measured figure matches the snapshot",
    "disclosure": "scores were reported with their assumptions",
    "sources": "data sources were attributed",
    "expected_phrasing": "the answer addressed what was asked",
    "no_forbidden_phrasing": "the answer avoided unsupported claims",
    "asked_for_clarification": "an ambiguous question was asked back, not guessed",
    "refused_as_out_of_scope": "an unsupported request was refused",
}


def render_report(results: list[CaseResult], model: str) -> str:
    passed = [r for r in results if r.passed]
    total_in = sum(r.input_tokens for r in results)
    total_out = sum(r.output_tokens for r in results)
    total_cached = sum(r.cached_tokens for r in results)
    total_time = sum(r.seconds for r in results)

    lines = [
        "# Evaluation results",
        "",
        f"- **Model:** `{model}`",
        f"- **Run:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- **Result:** {len(passed)}/{len(results)} cases passed",
        f"- **Wall time:** {total_time:.0f}s total, {total_time / max(len(results), 1):.1f}s per case",
        f"- **Tokens:** {total_in:,} in ({total_cached:,} from cache), {total_out:,} out",
        "",
        "Every case runs through the same `run_turn` the `/chat` endpoint calls —",
        "same tools, same scoring engine, same live NWS alerts. There is no",
        "separate evaluation implementation, and no LLM judge: a judge that grades",
        "an answer is another model output whose correctness would itself need",
        "grading.",
        "",
        "## Summary",
        "",
        "| Case | Category | Result | Checks | Time |",
        "| --- | --- | --- | --- | --- |",
    ]

    for result in results:
        status = "PASS" if result.passed else ("ERROR" if result.error else "FAIL")
        ok = sum(1 for c in result.checks if c.passed)
        lines.append(
            f"| `{result.case_id}` | {result.category} | **{status}** | "
            f"{ok}/{len(result.checks)} | {result.seconds:.1f}s |"
        )

    lines += ["", "## What each check means", ""]
    seen: set[str] = set()
    for result in results:
        for check in result.checks:
            if check.name not in seen:
                seen.add(check.name)
                lines.append(
                    f"- **`{check.name}`** — {CHECK_DESCRIPTIONS.get(check.name, '')}"
                )

    lines += ["", "## Case detail", ""]
    for result in results:
        status = "PASS" if result.passed else ("ERROR" if result.error else "FAIL")
        lines += [
            f"### `{result.case_id}` — {status}",
            "",
            f"> {result.question}",
            "",
            f"Intent: `{result.intent or 'n/a'}`",
            "",
        ]
        if result.error:
            lines += [f"**Harness error:** `{result.error}`", ""]
        if result.answer:
            answer = result.answer.strip().replace("\n", "\n> ")
            lines += ["**Answer**", "", f"> {answer}", ""]
        if result.checks:
            lines += ["| Check | Result | Detail |", "| --- | --- | --- |"]
            for check in result.checks:
                mark = "pass" if check.passed else "**fail**"
                lines.append(f"| `{check.name}` | {mark} | {check.detail} |")
            lines.append("")

    return "\n".join(lines) + "\n"
