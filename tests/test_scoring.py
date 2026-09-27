"""Tests for the deterministic engine.

The engine is the part of this system that must be right before an LLM is
allowed anywhere near it, so these tests assert on ARITHMETIC and on the
handling of absent inputs -- not on whether the output "looks reasonable".

Fixtures are hand-built rather than read from the real snapshot wherever the
test is about a rule, so a future refetch cannot silently change what a test
means.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.hubs import Hub, load_hubs
from app.scoring.engine import (
    band_for,
    load_weights,
    rank_hubs,
    score_hub,
)

WEIGHTS = load_weights()


@pytest.fixture(scope="module")
def nri() -> dict:
    return json.loads(pathlib.Path("data/nri_snapshot.json").read_text(encoding="utf-8"))


@pytest.fixture
def denver() -> Hub:
    hub = load_hubs().by_id("denver-co")
    assert hub is not None
    return hub


@pytest.fixture
def miami() -> Hub:
    hub = load_hubs().by_id("miami-fl")
    assert hub is not None
    return hub


def _nri_record(**scores: float | None) -> dict:
    """An NRI record with exactly the hazard scores named."""
    hazards = {
        name: {"risk_score": scores.get(name), "risk_rating": None, "field_prefix": "XX"}
        for name in (
            "winter_weather", "cold_wave", "hurricane",
            "inland_flooding", "coastal_flooding",
        )
    }
    return {"hub_id": "test", "nri_fips": "00000", "hazards": hazards, "composite": {}}


class TestRenormalisation:
    """The central rule: an absent component is not a zero."""

    def test_absent_component_does_not_drag_the_score_down(self, denver: Hub) -> None:
        """Denver has no coastal flooding score. Its flood result must be the
        inland score, not an average of inland and an imputed zero."""
        record = _nri_record(inland_flooding=80.0, coastal_flooding=None)
        result = score_hub(denver, "flood", record)

        baseline = next(c for c in result.components if c.name == "baseline")
        assert baseline.value == 80.0, "renormalised baseline must ignore the absent hazard"
        # The wrong answer here is 40.0 -- mean of 80 and an imputed 0.
        assert baseline.value != 40.0

    def test_absent_hazard_is_disclosed_not_hidden(self, denver: Hub) -> None:
        record = _nri_record(inland_flooding=80.0, coastal_flooding=None)
        result = score_hub(denver, "flood", record)
        assert any("coastal flooding" in a for a in result.assumptions)

    def test_unknown_alert_severity_is_excluded_and_disclosed(self, miami: Hub) -> None:
        """An alert whose severity NWS reports as Unknown is missing data. It
        must not score as zero, which would assert 'no current risk' from an
        alert that exists."""
        record = _nri_record(hurricane=100.0)
        alerts = ({"event": "Hurricane Warning", "severity": "Unknown"},)
        result = score_hub(miami, "hurricane", record, alerts)

        current = next(c for c in result.components if c.name == "current")
        assert current.value is None, "unknown severity must be absent, not zero"
        assert any("unknown" in a.lower() for a in result.assumptions)
        # Renormalised onto baseline alone.
        assert result.score == pytest.approx(100.0)

    def test_no_alerts_is_a_measurement_of_zero_not_an_absence(self, miami: Hub) -> None:
        """The distinction that makes the previous test meaningful: no warning
        at all is real information and scores 0 with full weight."""
        record = _nri_record(hurricane=100.0)
        result = score_hub(miami, "hurricane", record, ())

        current = next(c for c in result.components if c.name == "current")
        assert current.value == 0.0
        # 100 * 0.60 / 1.0, NOT renormalised away.
        assert result.score == pytest.approx(60.0)


class TestArithmetic:
    def test_score_is_the_weighted_mean_of_present_components(self, miami: Hub) -> None:
        record = _nri_record(hurricane=90.0)
        alerts = ({"event": "Hurricane Warning", "severity": "Extreme"},)
        result = score_hub(miami, "hurricane", record, alerts)

        weights = WEIGHTS["hazards"]["hurricane"]["weights"]
        expected = (90.0 * weights["baseline"] + 100.0 * weights["current"]) / (
            weights["baseline"] + weights["current"]
        )
        assert result.score == pytest.approx(round(expected, 1))

    def test_worst_alert_wins_rather_than_alerts_summing(self, miami: Hub) -> None:
        """Two simultaneous warnings do not double the disruption. Summing
        would let many advisories outrank one Extreme warning."""
        record = _nri_record(hurricane=50.0)
        many_minor = tuple(
            {"event": "High Wind Warning", "severity": "Minor"} for _ in range(6)
        )
        one_extreme = ({"event": "Hurricane Warning", "severity": "Extreme"},)

        assert (
            score_hub(miami, "hurricane", record, one_extreme).score
            > score_hub(miami, "hurricane", record, many_minor).score
        )

    def test_irrelevant_alerts_are_ignored_for_this_hazard(self, miami: Hub) -> None:
        record = _nri_record(hurricane=50.0)
        winter_alert = ({"event": "Winter Storm Warning", "severity": "Extreme"},)
        result = score_hub(miami, "hurricane", record, winter_alert)
        current = next(c for c in result.components if c.name == "current")
        assert current.value == 0.0

    def test_refuses_to_score_when_nothing_can_be_measured(self, miami: Hub) -> None:
        """A score with no inputs is worse than an error, because it looks
        like an answer."""
        record = _nri_record(hurricane=None)
        alerts = ({"event": "Hurricane Warning", "severity": "Unknown"},)
        with pytest.raises(ValueError, match="no component could be measured"):
            score_hub(miami, "hurricane", record, alerts)


class TestBands:
    def test_band_agrees_with_the_displayed_score(self, nri: dict) -> None:
        """Regression: bands were computed from the raw float while the score
        was rounded, so 59.99999 and 60.00001 both displayed as 60.0 in
        DIFFERENT bands. Two hubs showing the same number with different
        labels reads as a broken system."""
        registry = load_hubs()
        for hazard in ("winter", "hurricane", "flood"):
            for result in rank_hubs(registry.hubs, hazard, nri):
                assert result.band == band_for(result.score), (
                    f"{result.hub_label}/{hazard}: score {result.score} "
                    f"displayed as {result.band}"
                )

    def test_equal_scores_always_get_equal_bands(self, nri: dict) -> None:
        registry = load_hubs()
        by_score: dict[float, set[str]] = {}
        for hazard in ("winter", "hurricane", "flood"):
            for result in rank_hubs(registry.hubs, hazard, nri):
                by_score.setdefault(result.score, set()).add(result.band)
        collisions = {s: b for s, b in by_score.items() if len(b) > 1}
        assert not collisions, f"same score, different bands: {collisions}"


class TestHurricaneMethodology:
    """The hurricane hazard deliberately has no historical component."""

    def test_hurricane_has_two_components_not_three(self, miami: Hub) -> None:
        result = score_hub(miami, "hurricane", _nri_record(hurricane=100.0))
        assert [c.name for c in result.components] == ["baseline", "current"]

    def test_exclusion_is_explained_as_methodology_not_missing_data(
        self, miami: Hub
    ) -> None:
        """A reader must not conclude the data was unavailable."""
        result = score_hub(miami, "hurricane", _nri_record(hurricane=100.0))
        note = " ".join(result.assumptions).lower()
        assert "five-year" in note and "9 km" in note
        assert "could not be measured" not in note

    def test_gulf_and_florida_hubs_outrank_the_northeast(self, nri: dict) -> None:
        """The regression this methodology change fixed: a 58 mph gust proxy
        over five years ranked New York and Boston ABOVE Miami and Houston,
        because it was measuring nor'easters."""
        registry = load_hubs()
        ranking = [r.hub_id for r in rank_hubs(registry.hubs, "hurricane", nri)]
        for tropical in ("miami-fl", "houston-tx", "new-orleans-la", "tampa-fl"):
            assert ranking.index(tropical) < ranking.index("new-york-ny"), (
                f"{tropical} must rank above New York for hurricane exposure"
            )
            assert ranking.index(tropical) < ranking.index("boston-ma")


class TestRankingStability:
    def test_ranking_is_deterministic_across_runs(self, nri: dict) -> None:
        registry = load_hubs()
        first = [r.hub_id for r in rank_hubs(registry.hubs, "winter", nri)]
        second = [r.hub_id for r in rank_hubs(registry.hubs, "winter", nri)]
        assert first == second

    def test_ties_break_on_hub_id_not_input_order(self, nri: dict) -> None:
        registry = load_hubs()
        forward = rank_hubs(registry.hubs, "flood", nri)
        reversed_input = rank_hubs(tuple(reversed(registry.hubs)), "flood", nri)
        assert [r.hub_id for r in forward] == [r.hub_id for r in reversed_input]

    def test_midwest_winter_ranking_is_led_by_the_great_lakes(self, nri: dict) -> None:
        """A sanity anchor, deliberately loose: asserting an exact order would
        pin the weights, and the weights are meant to be adjustable."""
        registry = load_hubs()
        midwest = tuple(h for h in registry.hubs if h.region == "Midwest")
        top_three = {r.hub_id for r in rank_hubs(midwest, "winter", nri)[:3]}
        assert top_three & {"milwaukee-wi", "minneapolis-mn", "chicago-il", "detroit-mi"}


class TestTopDriver:
    def test_top_driver_is_the_largest_contribution(self, miami: Hub) -> None:
        """'Which component contributed most?' must be a lookup, never a
        judgement the model makes."""
        record = _nri_record(hurricane=10.0)
        alerts = ({"event": "Hurricane Warning", "severity": "Extreme"},)
        result = score_hub(miami, "hurricane", record, alerts)
        # 10 * 0.60 = 6.0 vs 100 * 0.40 = 40.0
        assert result.top_driver is not None
        assert result.top_driver.name == "current"
