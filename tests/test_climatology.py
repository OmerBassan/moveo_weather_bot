"""Tests for day-counting, and for the two-threshold split.

The split is the point: the same series answers "what percentage of days had
snowfall" and "how often is this hub disrupted by snow" with different
numbers, and both are correct.
"""

from __future__ import annotations

import pytest

from app.config import (
    SNOW_DAY_DISRUPTION_THRESHOLD_IN,
    SNOW_DAY_LITERAL_THRESHOLD_IN,
)
from app.scoring import climatology
from app.scoring.climatology import (
    SNOWFALL,
    DayCount,
    YearCount,
    YearlySeries,
    available_years,
    count_days,
    count_days_by_year,
)


class TestLiteralVersusDisruption:
    def test_literal_and_disruption_counts_differ_and_both_are_right(self) -> None:
        """The assignment's own example question, on real data."""
        literal = climatology.count_days(
            "denver-co", climatology.SNOWFALL,
            SNOW_DAY_LITERAL_THRESHOLD_IN, year="2025", strict=True,
        )
        disruptive = climatology.count_days(
            "denver-co", climatology.SNOWFALL,
            SNOW_DAY_DISRUPTION_THRESHOLD_IN, year="2025",
        )
        assert literal.days_matching > disruptive.days_matching
        assert 0 < literal.percent_of_observed < 100

    def test_literal_threshold_is_strictly_greater_than_zero(self) -> None:
        """`>= 0` would match every day in the year, including dry ones."""
        strict = climatology.count_days(
            "miami-fl", climatology.SNOWFALL, 0.0, year="2025", strict=True
        )
        inclusive = climatology.count_days(
            "miami-fl", climatology.SNOWFALL, 0.0, year="2025"
        )
        assert strict.days_matching == 0, "Miami records no snowfall days"
        assert inclusive.days_matching == inclusive.days_observed


class TestDenominators:
    def test_percentage_divides_by_observed_days_not_calendar_days(self) -> None:
        """A gap in the data must not quietly deflate a frequency."""
        count = climatology.count_days(
            "denver-co", climatology.SNOWFALL, 1.0, year="2025"
        )
        assert count.days_observed + count.days_missing == count.days_total
        expected = count.days_matching / count.days_observed * 100.0
        assert count.percent_of_observed == pytest.approx(expected)

    def test_2025_has_365_days(self) -> None:
        count = climatology.count_days(
            "chicago-il", climatology.SNOWFALL, 1.0, year="2025"
        )
        assert count.days_total == 365

    def test_evidence_line_reports_every_figure_it_uses(self) -> None:
        """The narrator quotes this verbatim, so it must be self-contained."""
        count = climatology.count_days(
            "buffalo-ny", climatology.SNOWFALL, 1.0, year="2025"
        )
        line = count.evidence()
        assert str(count.days_matching) in line
        assert str(count.days_observed) in line
        assert "2025" in line


class TestFailureModes:
    def test_unknown_hub_raises_rather_than_returning_zero(self) -> None:
        """Returning a zero count for a hub that does not exist would answer
        'no snow there' about a place the system has never heard of."""
        with pytest.raises(KeyError):
            climatology.count_days("atlantis-xx", climatology.SNOWFALL, 1.0)

    def test_unknown_variable_raises(self) -> None:
        with pytest.raises(KeyError):
            climatology.count_days("denver-co", "humidity_pct", 1.0)


class TestProvenance:
    def test_every_hub_reports_its_grid_cell(self) -> None:
        provenance = climatology.provenance("denver-co")
        assert provenance["grid_cell_offset_km"] >= 0
        assert provenance["requested_latitude"] != provenance["returned_latitude"]

    def test_record_runs_from_2021_to_the_current_year(self) -> None:
        """The window is extended by re-running scripts.fetch_history, so this
        asserts the shape rather than a fixed end date -- it must not start
        failing simply because the record was brought up to date."""
        from datetime import date

        years = climatology.available_years("denver-co")
        assert years[0] == "2021"
        assert int(years[-1]) >= date.today().year - 1
        assert len(years) == int(years[-1]) - int(years[0]) + 1, "a year is missing"


class TestDirection:
    """The threshold says WHERE the line is; the direction says which side of it
    counts. These are the regression tests for a live wrong answer: the
    `freezing` metric was counted with `>=`, so the agent reported that Dallas
    froze on 335 days of 2025 and Chicago on 269 -- both of them actually the
    count of days that did NOT freeze, which is why the milder hub came out
    colder."""

    def test_freezing_counts_cold_days_not_mild_ones(self) -> None:
        cold = climatology.count_days(
            "chicago-il", climatology.TEMP_MIN, 32.0,
            year="2025", direction=climatology.BELOW,
        )
        mild = climatology.count_days(
            "chicago-il", climatology.TEMP_MIN, 32.0,
            year="2025", direction=climatology.ABOVE,
        )
        # A 32F boundary day satisfies both inclusive comparisons, so the two
        # counts overlap rather than partition -- the sum is >= the year.
        assert cold.days_matching + mild.days_matching >= cold.days_observed
        assert 0 < cold.days_matching < mild.days_matching, (
            "most of a Chicago year is above freezing at night"
        )

    def test_the_colder_hub_records_more_freezing_days(self) -> None:
        """The check the broken version failed: ordering two hubs whose relative
        climates are not in question."""
        chicago = climatology.count_days(
            "chicago-il", climatology.TEMP_MIN, 32.0,
            year="2025", direction=climatology.BELOW,
        )
        dallas = climatology.count_days(
            "dallas-tx", climatology.TEMP_MIN, 32.0,
            year="2025", direction=climatology.BELOW,
        )
        assert chicago.days_matching > dallas.days_matching

    def test_evidence_line_prints_the_operator_it_applied(self) -> None:
        """The narrator quotes this line, and the agent visibly distrusted the
        answer because the wording contradicted the metric."""
        count = climatology.count_days(
            "denver-co", climatology.TEMP_MIN, 32.0,
            year="2025", direction=climatology.BELOW,
        )
        assert "<= 32.0" in count.evidence()
        strict = climatology.count_days(
            "denver-co", climatology.TEMP_MIN, 32.0,
            year="2025", direction=climatology.BELOW, strict=True,
        )
        assert "< 32.0" in strict.evidence()
        assert strict.days_matching <= count.days_matching

    def test_unknown_direction_raises_rather_than_guessing(self) -> None:
        with pytest.raises(ValueError):
            climatology.count_days(
                "denver-co", climatology.TEMP_MIN, 32.0, direction="downwards"
            )


class TestMetricDirectionsAreDeclared:
    """Every named metric the agent can ask for must be counted on the side its
    own description promises. This is the test that generalises the fix beyond
    the one metric that was wrong."""

    def test_every_metric_direction_matches_its_description(self) -> None:
        from app.agent.tools import MEASUREMENT_METRICS

        for name, spec in MEASUREMENT_METRICS.items():
            says_below = "below" in spec.description or "under" in spec.description
            expected = climatology.BELOW if says_below else climatology.ABOVE
            assert spec.direction == expected, (
                f"metric {name!r} is described as counting days "
                f"{spec.description!r} but is counted {spec.direction}"
            )

    def test_minimum_temperature_is_never_counted_upwards(self) -> None:
        """A minimum-temperature metric counted with `>=` measures mildness. If
        one is ever added, it must say so explicitly, not inherit the default."""
        from app.agent.tools import MEASUREMENT_METRICS

        for name, spec in MEASUREMENT_METRICS.items():
            if spec.variable == climatology.TEMP_MIN:
                assert spec.direction == climatology.BELOW, name


# ------------------------------------------------- year-by-year reportage --


def _series(counts: dict[str, int], *, incomplete: tuple[str, ...] = ()) -> YearlySeries:
    """Build a series directly, so the edge cases below do not depend on what
    the weather happened to do in the snapshot."""
    return YearlySeries(
        hub_id="synthetic",
        variable=SNOWFALL,
        threshold=1.0,
        comparison=">=",
        years=tuple(
            YearCount(
                year=year,
                count=DayCount(
                    hub_id="synthetic",
                    variable=SNOWFALL,
                    threshold=1.0,
                    comparison=">=",
                    days_matching=days,
                    days_observed=365,
                    days_missing=0,
                    period_label=year,
                ),
                complete=year not in incomplete,
            )
            for year, days in counts.items()
        ),
    )


class TestWorstYear:
    def test_it_is_the_complete_year_with_the_most_days(self) -> None:
        series = _series({"2021": 8, "2022": 12, "2023": 16, "2024": 6, "2025": 8})
        worst = series.worst_year
        assert worst is not None
        assert worst.year == "2023"
        assert worst.count.days_matching == 16

    def test_a_partial_year_can_never_win(self) -> None:
        """THE FAILURE THIS PREVENTS: a partial year with the highest raw count
        would otherwise be reported as the worst year on record, when the
        comparison is simply not available yet. 2026 here has more days than
        any complete year and must still lose."""
        series = _series(
            {"2021": 8, "2022": 12, "2023": 16, "2024": 6, "2025": 8, "2026": 99},
            incomplete=("2026",),
        )
        worst = series.worst_year
        assert worst is not None
        assert worst.year == "2023"
        assert [year.year for year in series.partial_years] == ["2026"]

    def test_a_tie_resolves_to_the_earlier_year(self) -> None:
        """Stability across runs: a worst year that alternates between two
        equal candidates looks like a bug to anyone comparing two answers."""
        series = _series({"2021": 10, "2022": 5, "2023": 10})
        worst = series.worst_year
        assert worst is not None
        assert worst.year == "2021"

    def test_no_complete_year_yields_none_not_zero(self) -> None:
        series = _series({"2026": 4}, incomplete=("2026",))
        assert series.worst_year is None
        assert series.mean_days is None


class TestSlope:
    def test_it_is_none_below_five_complete_years(self) -> None:
        """Four points do not describe a trend, and a number here would be
        quoted as though they did."""
        series = _series({"2022": 1, "2023": 2, "2024": 3, "2025": 4})
        assert series.slope_days_per_year is None

    def test_a_partial_year_does_not_make_up_the_fifth_point(self) -> None:
        series = _series(
            {"2022": 1, "2023": 2, "2024": 3, "2025": 4, "2026": 5},
            incomplete=("2026",),
        )
        assert series.slope_days_per_year is None

    def test_a_clean_rise_measures_one_day_per_year(self) -> None:
        series = _series({"2021": 1, "2022": 2, "2023": 3, "2024": 4, "2025": 5})
        assert series.slope_days_per_year == pytest.approx(1.0)

    def test_a_flat_record_measures_zero(self) -> None:
        series = _series({"2021": 7, "2022": 7, "2023": 7, "2024": 7, "2025": 7})
        assert series.slope_days_per_year == pytest.approx(0.0)

    def test_the_slope_always_carries_its_objection(self) -> None:
        """A slope over five points ships with the same argument that removed
        the hurricane historical component, or it does not ship."""
        series = _series({"2021": 1, "2022": 2, "2023": 3, "2024": 4, "2025": 5})
        notes = " ".join(series.assumptions())
        assert "not a forecast" in notes
        assert "hurricane" in notes
        assert "not an input" in notes


class TestAgainstTheRealSnapshot:
    def test_every_available_year_is_counted(self) -> None:
        series = count_days_by_year("minneapolis-mn", SNOWFALL, 1.0)
        assert [year.year for year in series.years] == list(
            available_years("minneapolis-mn")
        )

    def test_the_partial_year_is_disclosed(self) -> None:
        """The snapshot window ends mid-year, so there is always one."""
        series = count_days_by_year("minneapolis-mn", SNOWFALL, 1.0)
        assert series.partial_years
        assert any("does not cover all of" in note for note in series.assumptions())

    def test_per_year_counts_sum_to_the_whole_window_count(self) -> None:
        """The per-year path must agree with the single-window path, or the
        engine's historical component and this reportage describe different
        weather."""
        series = count_days_by_year("chicago-il", SNOWFALL, 1.0)
        whole = count_days("chicago-il", SNOWFALL, 1.0)
        assert sum(y.count.days_matching for y in series.years) == whole.days_matching

    def test_a_hub_where_it_never_happens_says_so_plainly(self) -> None:
        series = count_days_by_year("miami-fl", SNOWFALL, 1.0)
        assert series.worst_year is not None
        assert series.worst_year.count.days_matching == 0
        assert any("no day in" in line for line in series.evidence())

    def test_evidence_quotes_only_figures_it_holds(self) -> None:
        """Every number in a line must be traceable to the DayCount it came
        from -- these strings are quoted verbatim by the narrator."""
        series = count_days_by_year("minneapolis-mn", SNOWFALL, 1.0)
        for year in series.years:
            assert any(
                f"{year.year}: {year.count.days_matching} day(s)" in line
                for line in series.evidence()
            )
