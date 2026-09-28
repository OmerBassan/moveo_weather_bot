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
    HAZARDS,
    _tier_rank,
    band_for,
    load_weights,
    rank_hubs,
    rank_portfolio,
    score_hub,
    structural_cutoff,
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


class TestStructuralAndTransient:
    """The investment reading: what a resilience upgrade could act on, split
    from what is merely happening this week."""

    def test_structural_excludes_the_current_component(self, denver: Hub) -> None:
        record = _nri_record(winter_weather=80.0, cold_wave=40.0)
        alerts = ({"event": "Blizzard Warning", "severity": "Extreme"},)
        result = score_hub(denver, "winter", record, alerts)

        weights = WEIGHTS["hazards"]["winter"]["weights"]
        baseline = next(c for c in result.components if c.name == "baseline")
        historical = next(c for c in result.components if c.name == "historical")
        expected = (
            baseline.value * weights["baseline"] + historical.value * weights["historical"]
        ) / (weights["baseline"] + weights["historical"])

        assert result.structural_score == pytest.approx(round(expected, 1))
        # ...and the Extreme warning shows up only in the transient reading.
        assert result.transient_score == 100.0
        assert result.structural_score < result.score

    def test_structural_is_unmoved_by_any_amount_of_live_weather(
        self, denver: Hub
    ) -> None:
        """THE LOAD-BEARING CLAIM. If live weather could move the structural
        score, the whole split would be decorative and the tier would drift
        with the forecast."""
        record = _nri_record(winter_weather=80.0, cold_wave=40.0)
        quiet = score_hub(denver, "winter", record, ())
        storm = score_hub(
            denver,
            "winter",
            record,
            ({"event": "Blizzard Warning", "severity": "Extreme"},),
            forecast={"snowfall_in": 24.0, "window_hours": 72},
        )

        assert quiet.structural_score == storm.structural_score
        # The TIER may legitimately move Low -> Watch, which is what Watch is
        # for. What weather must never do is promote a hub INTO the Invest
        # group, because that group is the capital-allocation shortlist.
        assert quiet.investability != "Invest"
        assert storm.investability != "Invest"
        assert storm.investability == "Watch"
        # While the headline score and the transient reading both moved a lot.
        assert storm.score > quiet.score
        assert storm.transient_score == 100.0
        assert quiet.transient_score == 0.0

    def test_hurricane_structural_is_the_baseline_itself(self, miami: Hub) -> None:
        """Hurricane declares no historical component, so renormalising over
        the structural set leaves a weight of 0.60 carrying the whole thing --
        which must give back the baseline value, not 60% of it."""
        record = _nri_record(hurricane=90.0)
        result = score_hub(miami, "hurricane", record, ())
        assert result.structural_score == pytest.approx(90.0)

    def test_no_structural_component_gives_none_not_zero(self, miami: Hub) -> None:
        """FEMA models no hurricane risk for eight of these counties, and
        hurricane has no historical component, so there is nothing structural
        to report. Zero would assert 'no persistent exposure'; the total score
        would let the live wind reading pose as one."""
        record = _nri_record(hurricane=None)
        alerts = ({"event": "High Wind Warning", "severity": "Severe"},)
        result = score_hub(miami, "hurricane", record, alerts)

        assert result.structural_score is None
        assert result.structural_band is None
        assert result.investability is None
        # The score itself is entirely the live reading -- the pathology this
        # split exists to make visible.
        assert result.score == pytest.approx(75.0)
        assert result.transient_score == 75.0

    def test_unknown_severity_leaves_the_transient_reading_absent(
        self, miami: Hub
    ) -> None:
        record = _nri_record(hurricane=100.0)
        alerts = ({"event": "Hurricane Warning", "severity": "Unknown"},)
        result = score_hub(miami, "hurricane", record, alerts)
        assert result.transient_score is None
        assert result.transient_band is None

    def test_flood_structural_survives_an_absent_coastal_component(
        self, denver: Hub
    ) -> None:
        """Inland flooding is modelled for every hub in the network, so a flood
        structural score is never None even though nineteen hubs have no
        coastal value."""
        record = _nri_record(inland_flooding=80.0, coastal_flooding=None)
        result = score_hub(denver, "flood", record)
        assert result.structural_score is not None


class TestInvestability:
    """The tier is a standing within the network, and the cutoff is a property
    of the frozen record rather than of the question asked."""

    def test_invest_is_the_networks_top_third_for_each_hazard(
        self, nri: dict
    ) -> None:
        registry = load_hubs()
        one_in = int(WEIGHTS["investability"]["invest_top_one_in"])
        for hazard in HAZARDS:
            results = rank_hubs(registry.hubs, hazard, nri)
            tierable = [r for r in results if r.structural_score is not None]
            invest = [r for r in results if r.investability == "Invest"]
            # Floor division, with ties at the boundary joining the group.
            assert len(invest) >= max(1, len(tierable) // one_in)
            assert len(invest) < len(tierable), (
                f"{hazard}: a tier covering the whole network is not a shortlist"
            )
            # Every Invest hub outranks every non-Invest one on structural score.
            floor = min(r.structural_score for r in invest)
            assert all(
                r.structural_score < floor
                for r in tierable
                if r.investability != "Invest"
            )

    def test_the_tier_discriminates_rather_than_decorating(self, nri: dict) -> None:
        """REGRESSION ON A REAL FAILURE. The first rule used the absolute band
        boundaries -- structural in High or Very High -- and tiered 88 of 112
        hub-hazard pairs as Invest, because FEMA's NRI is a loss-weighted
        national percentile and every hub here is a major metro county. A
        shortlist holding five hubs out of six is not a shortlist."""
        registry = load_hubs()
        invest = total = 0
        for hazard in HAZARDS:
            for result in rank_hubs(registry.hubs, hazard, nri):
                if result.structural_score is None:
                    continue
                total += 1
                invest += result.investability == "Invest"
        assert invest / total < 0.45, f"{invest} of {total} tiered Invest"

    def test_the_tier_does_not_depend_on_which_hubs_were_asked_about(
        self, nri: dict, miami: Hub, denver: Hub
    ) -> None:
        """Tiering against the queried subset would make both hubs in any
        two-hub comparison top-half by arithmetic, and the same hub would carry
        different tiers in consecutive sentences."""
        registry = load_hubs()
        whole_network = {
            r.hub_id: r.investability for r in rank_hubs(registry.hubs, "flood", nri)
        }
        pair = rank_hubs((miami, denver), "flood", nri)
        for result in pair:
            assert result.investability == whole_network[result.hub_id]

    def test_watch_marks_a_hub_outside_the_invest_group_with_live_weather(
        self, nri: dict
    ) -> None:
        """Watch exists so that a hub the structural record would not fund is
        still visible when something is actually happening to it."""
        registry = load_hubs()
        quiet = {r.hub_id: r for r in rank_hubs(registry.hubs, "winter", nri)}
        outside = next(r for r in quiet.values() if r.investability == "Low")

        hub = registry.by_id(outside.hub_id)
        assert hub is not None
        stormy = score_hub(
            hub,
            "winter",
            nri["hubs"][hub.id],
            ({"event": "Winter Storm Warning", "severity": "Extreme"},),
        )
        assert stormy.investability == "Watch"
        # And it did NOT become Invest: a passing storm is not a capital case.
        assert stormy.investability != "Invest"

    def test_no_tier_when_the_live_reading_is_needed_but_absent(
        self, nri: dict
    ) -> None:
        """Outside the Invest group the tier turns on the transient reading, so
        an unmeasurable one leaves the tier genuinely undetermined. Returning
        'Low' would answer a test that was never run."""
        registry = load_hubs()
        quiet = {r.hub_id: r for r in rank_hubs(registry.hubs, "winter", nri)}
        outside = next(r for r in quiet.values() if r.investability == "Low")
        hub = registry.by_id(outside.hub_id)
        assert hub is not None

        result = score_hub(
            hub,
            "winter",
            nri["hubs"][hub.id],
            ({"event": "Winter Storm Warning", "severity": "Unknown"},),
        )
        assert result.transient_score is None
        assert result.investability is None

    def test_an_invest_hub_keeps_its_tier_with_no_live_reading_at_all(
        self, nri: dict
    ) -> None:
        """Inside the Invest group the transient reading is irrelevant, so an
        unmeasurable one must NOT erase the tier -- the structural case stands
        on its own."""
        registry = load_hubs()
        quiet = {r.hub_id: r for r in rank_hubs(registry.hubs, "winter", nri)}
        inside = next(r for r in quiet.values() if r.investability == "Invest")
        hub = registry.by_id(inside.hub_id)
        assert hub is not None

        result = score_hub(
            hub,
            "winter",
            nri["hubs"][hub.id],
            ({"event": "Winter Storm Warning", "severity": "Unknown"},),
        )
        assert result.transient_score is None
        assert result.investability == "Invest"

    def test_the_cutoff_reads_no_live_data(self) -> None:
        """It is cached for the life of the process, which is only sound
        because it depends on the frozen snapshots alone."""
        first = {h: structural_cutoff(h) for h in HAZARDS}
        second = {h: structural_cutoff(h) for h in HAZARDS}
        assert first == second
        assert all(value is not None for value in first.values())


@pytest.fixture(scope="module")
def entries(nri: dict) -> tuple:
    return rank_portfolio(load_hubs().hubs, nri)


class TestPortfolio:
    """The cross-hazard shortlist. Its whole job is to be ordered by something
    that means the same thing for every hazard."""

    def test_every_hub_appears_exactly_once(self, entries: tuple) -> None:
        ids = [e.hub_id for e in entries]
        assert len(ids) == len(set(ids)) == len(load_hubs().hubs)

    def test_tiers_come_in_order(self, entries: tuple) -> None:
        ranks = [_tier_rank(e.investability) for e in entries]
        assert ranks == sorted(ranks), "a lower tier appeared above a higher one"

    def test_breadth_outranks_depth_within_a_tier(self, entries: tuple) -> None:
        """A hub in the Invest group on three hazards has a broader case than
        one qualifying on a single hazard, and a COUNT compares cleanly across
        hazards where magnitudes do not."""
        invest = [e for e in entries if e.investability == "Invest"]
        counts = [len(e.driving_hazards) for e in invest]
        assert counts == sorted(counts, reverse=True)
        assert counts[0] == 3, "expected at least one hub Invest on all three"

    def test_the_order_is_not_raw_structural_score(self, entries: tuple) -> None:
        """REGRESSION ON A MEASURED FAILURE. Ordering the Invest group by raw
        structural score compared distributions rather than hubs: winter's
        cutoff is 73.7 and flood's 87.9, so all ten winter-only hubs sorted
        below every flood hub and Salt Lake City -- the most exposed winter hub
        in the network -- landed ninth. At least one pair must now be
        'out of order' by raw score for the normalisation to be doing anything.
        """
        invest = [e for e in entries if e.investability == "Invest"]
        raw = [e.structural_score for e in invest]
        assert raw != sorted(raw, reverse=True)

    def test_exceedance_is_measured_against_the_hazards_own_cutoff(
        self, entries: tuple
    ) -> None:
        for entry in entries:
            if entry.exceedance is None:
                continue
            best = max(
                (
                    result.structural_score / structural_cutoff(result.hazard)
                    for result in entry.by_hazard
                    if result.structural_score is not None
                    and result.hazard in entry.driving_hazards
                    and structural_cutoff(result.hazard)
                ),
                default=None,
            )
            assert best is not None
            assert entry.exceedance == pytest.approx(round(best, 3))

    def test_an_invest_hub_exceeds_its_cutoff_by_definition(
        self, entries: tuple
    ) -> None:
        for entry in entries:
            if entry.investability == "Invest":
                assert entry.exceedance is not None and entry.exceedance >= 1.0

    def test_driving_hazards_names_all_of_them_not_a_winner(
        self, entries: tuple
    ) -> None:
        """When a hub reaches its best tier on two hazards, naming one and
        hiding the other is a judgement the data does not support."""
        multi = [e for e in entries if len(e.driving_hazards) > 1]
        assert multi, "expected at least one hub driven by more than one hazard"
        for entry in multi:
            tiers = {
                result.hazard: result.investability
                for result in entry.by_hazard
                if result.hazard in entry.driving_hazards
            }
            assert len(set(tiers.values())) == 1, "driving hazards must share a tier"

    def test_it_carries_the_three_disclosures_that_make_it_readable(
        self, entries: tuple
    ) -> None:
        """Without the first of these, the first analyst to see a high-scoring
        hub tiered Low files a defect."""
        notes = " ".join(entries[0].assumptions)
        assert "not by today's risk score" in notes
        assert "not an absolute level of risk" in notes
        assert "NOT compared across hazards" in notes

    def test_the_ordering_is_stable_across_runs(self, nri: dict) -> None:
        first = rank_portfolio(load_hubs().hubs, nri)
        second = rank_portfolio(load_hubs().hubs, nri)
        assert [e.hub_id for e in first] == [e.hub_id for e in second]

    def test_live_weather_cannot_promote_a_hub_into_the_invest_group(
        self, nri: dict
    ) -> None:
        """THE CLAIM THE WHOLE FEATURE RESTS ON. A storm must be able to change
        a hub's risk score and its Watch status, and must never change who is a
        candidate for capital."""
        registry = load_hubs()
        calm = rank_portfolio(registry.hubs, nri)
        calm_invest = {e.hub_id for e in calm if e.investability == "Invest"}

        # An Extreme alert of every relevant kind, at every hub at once.
        everything = {
            hub.id: (
                {"event": "Blizzard Warning", "severity": "Extreme"},
                {"event": "Hurricane Warning", "severity": "Extreme"},
                {"event": "Flash Flood Warning", "severity": "Extreme"},
            )
            for hub in registry.hubs
        }
        stormy = rank_portfolio(registry.hubs, nri, alerts_by_hub=everything)
        stormy_invest = {e.hub_id for e in stormy if e.investability == "Invest"}

        assert stormy_invest == calm_invest
        # ...while the risk scores did move, so the test is not vacuous.
        calm_scores = {e.hub_id: e.score for e in calm}
        assert any(e.score > calm_scores[e.hub_id] for e in stormy)
