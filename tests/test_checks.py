"""Tests for the deterministic evaluation instruments themselves.

The groundedness check is the only thing standing between a fabricated figure
and a passing evaluation, so it needs its own tests: an instrument nobody tests
reports whatever its bugs believe. These are written against the two failures
it actually had -- a blanket exemption for small integers, and ignoring the
`interpretation` field entirely.
"""

from __future__ import annotations

from evals import checks


def response(answer: str = "", interpretation: tuple[str, ...] = ()) -> dict:
    """A minimal response carrying one real score, so `allowed` is not empty."""
    return {
        "answer": answer,
        "interpretation": list(interpretation),
        "assessments": [
            {
                "hub_id": "dallas-tx",
                "hazard": "winter",
                "risk_score": 62.4,
                "rank": 1,
                "gap_to_leader": 0.0,
                "gap_to_next": 4.6,
                "component_breakdown": [
                    "baseline: 71.0 x 0.50 = 35.5 pts, 57% of the 62.4 score (max of "
                    "winter weather=71.0, cold wave=44.0)"
                ],
                "evidence": [
                    "Dallas, TX: 3.2 days/year with snowfall_in >= 1.0 (16 of 2089 "
                    "observed days (0.8%) in 2021-01-01 to 2026-09-20 had snowfall_in "
                    ">= 1.0)"
                ],
            }
        ],
    }


class TestFabricatedOperationalFigures:
    """The reason the exemption had to be narrowed. Every figure here is one the
    system holds no data to produce."""

    def test_invented_delay_count_is_caught(self) -> None:
        result = checks.check_groundedness(
            response("Dallas should expect 8 delays next month."), set()
        )
        assert not result.passed
        assert "8" in result.detail

    def test_invented_closure_days_are_caught(self) -> None:
        result = checks.check_groundedness(
            response("Expect 7 closure days at the Dallas hub."), set()
        )
        assert not result.passed

    def test_a_figure_colliding_with_a_real_one_is_a_STATED_LIMIT(self) -> None:
        """Not a catch, and the docstring says so.

        The fixture's evidence carries 3.2 days/year, and the check accepts a
        value rounded to the precision shown -- so an invented "3 closures"
        is indistinguishable from a correct restatement of 3.2. Pinned as a
        test because a limitation that is only written in a comment gets
        quietly assumed away later.
        """
        result = checks.check_groundedness(
            response("Expect 3 closure days at the Dallas hub."), set()
        )
        assert result.passed

    def test_invented_downtime_hours_are_caught(self) -> None:
        result = checks.check_groundedness(
            response("This translates to roughly 12 hours of downtime."), set()
        )
        assert not result.passed

    def test_an_ungrounded_day_count_is_caught(self) -> None:
        """A day count IS the measurement on this path, so a wrong one must not
        pass merely for being small."""
        result = checks.check_groundedness(
            response("Dallas recorded 9 days of snowfall."), set()
        )
        assert not result.passed


class TestProseIsStillProse:
    """The exemption exists for a reason; narrowing it must not start failing
    answers that were always correct."""

    def test_a_tally_of_hubs_passes(self) -> None:
        result = checks.check_groundedness(
            response("All 10 Midwest hubs were scored; the top 3 are shown."), set()
        )
        assert result.passed, result.detail

    def test_a_rank_passes(self) -> None:
        result = checks.check_groundedness(
            response("Dallas, TX ranks 1 of 40 in the network."), set()
        )
        assert result.passed, result.detail

    def test_a_relative_window_passes(self) -> None:
        """'over the next 3 days' names a period; it asserts no measurement."""
        result = checks.check_groundedness(
            response("No alerts are active over the next 3 days."), set()
        )
        assert result.passed, result.detail

    def test_a_year_passes(self) -> None:
        result = checks.check_groundedness(
            response("In 2025 the hub saw no qualifying days."), set()
        )
        assert result.passed, result.detail

    def test_a_grounded_day_count_passes(self) -> None:
        """Same sentence shape as the caught case, with the figure supplied."""
        result = checks.check_groundedness(
            response("Dallas recorded 16 days of snowfall."), {16.0}
        )
        assert result.passed, result.detail

    def test_figures_from_the_breakdown_pass(self) -> None:
        result = checks.check_groundedness(
            response(
                "Dallas, TX scores 62.4, with the baseline contributing 35.5 points, "
                "57% of the total, and 4.6 points separating it from the next hub."
            ),
            set(),
        )
        assert result.passed, result.detail


class TestInterpretationIsChecked:
    """The field the prompt pushes every explanatory sentence into was exempt."""

    def test_an_invented_figure_in_interpretation_is_caught(self) -> None:
        result = checks.check_groundedness(
            response("Dallas leads the winter ranking.", ("Driven by 47 freezing nights.",)),
            set(),
        )
        assert not result.passed
        assert "interpretation" in result.detail

    def test_a_grounded_figure_in_interpretation_passes(self) -> None:
        result = checks.check_groundedness(
            response("Dallas leads the winter ranking.", ("Its baseline of 71.0 dominates.",)),
            set(),
        )
        assert result.passed, result.detail

    def test_the_failing_field_is_named(self) -> None:
        result = checks.check_groundedness(
            response("Expect 8 delays.", ("And 9 closures.",)), set()
        )
        assert not result.passed
        assert "answer" in result.detail and "interpretation" in result.detail
