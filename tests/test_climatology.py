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
