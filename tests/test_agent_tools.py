"""Tests for the agent-facing tool boundary.

These pin the guarantees that are supposed to hold WITHOUT the model
cooperating: that a count carries its caveats whether or not the model repeats
them, that the methodology the agent can quote is the config the engine
actually runs on, that a metric counts the side of its threshold its own
description promises, and that a tie at a ranking cut-off is visible.

No network: forecasts are pre-seeded so `ensure_forecasts` finds nothing
missing, and no test here depends on today's weather.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from typing import Any

from app.agent.contract import AgentDraft
from app.agent.runner import assemble
from app.agent.tools import (
    MEASUREMENT_METRICS,
    Deps,
    count_weather_days,
    describe_methodology,
    rank_hubs_by_risk,
)
from app.hubs import load_hubs
from app.scoring import climatology
from app.scoring.engine import load_weights


@pytest.fixture(scope="module")
def nri() -> dict:
    return json.loads(pathlib.Path("data/nri_snapshot.json").read_text(encoding="utf-8"))


def offline_deps(nri: dict) -> Deps:
    """Deps that cannot reach the network: every hub already has a forecast."""
    deps = Deps(nri=nri, registry=load_hubs())
    deps.forecasts = {hub.id: {} for hub in deps.registry.hubs}
    return deps


class TestMetricPairs:
    """The literal/operational split has to exist for every variable that has
    an operational threshold -- snowfall had both readings and precipitation had
    only the storm-sized one, so "how many days did it rain" was answered at a
    one-inch threshold."""

    def test_companions_are_symmetric(self) -> None:
        for name, spec in MEASUREMENT_METRICS.items():
            if spec.companion:
                other = MEASUREMENT_METRICS[spec.companion]
                assert other.companion == name, f"{name} -> {spec.companion} is one-way"
                assert other.variable == spec.variable

    def test_every_judgement_threshold_has_a_literal_counterpart(self) -> None:
        """An operational threshold is defensible only next to the literal count
        it departs from."""
        for name, spec in MEASUREMENT_METRICS.items():
            if spec.threshold_is_judgement and spec.variable != climatology.WIND_GUST_MAX:
                assert spec.companion, (
                    f"{name} counts at a judged threshold with no literal companion"
                )

    def test_literal_rain_and_heavy_rain_differ_by_a_lot(self, nri: dict) -> None:
        """The number the missing metric was getting wrong, on real data."""
        literal = count_weather_days(offline_deps(nri), "seattle-wa", "any_precipitation", year="2025")
        heavy = count_weather_days(offline_deps(nri), "seattle-wa", "heavy_precipitation", year="2025")
        assert literal["days_matching"] > heavy["days_matching"] * 10
        assert literal["companion_metric"]["metric"] == "heavy_precipitation"


class TestMeasurementDisclosure:
    """The count path's caveats are attached by the application, not by the
    model choosing to repeat them."""

    def test_assemble_attaches_them_without_any_model_cooperation(self, nri: dict) -> None:
        deps = offline_deps(nri)
        count_weather_days(deps, "denver-co", "any_snowfall", year="2025")
        draft = AgentDraft(
            interpretation_of_question="Snow days in Denver in 2025.",
            intent="measure",
            answer="12.1% of days.",
        )
        response = assemble(draft, deps)
        assert response.assessments == ()
        assert response.assumptions, "a count answer published with no caveats"
        assert any("grid cell" in a for a in response.assumptions)

    def test_a_partial_year_says_so(self, nri: dict) -> None:
        deps = offline_deps(nri)
        current = climatology.available_years("denver-co")[-1]
        assert not climatology.year_is_complete(current)
        payload = count_weather_days(deps, "denver-co", "any_snowfall", year=current)
        assert any("incomplete" in a for a in payload["assumptions"])

    def test_a_complete_year_does_not(self, nri: dict) -> None:
        payload = count_weather_days(offline_deps(nri), "denver-co", "any_snowfall", year="2025")
        assert not any("incomplete" in a for a in payload["assumptions"])

    def test_an_operational_threshold_is_named_as_a_judgement(self, nri: dict) -> None:
        payload = count_weather_days(
            offline_deps(nri), "denver-co", "disruptive_snowfall", year="2025"
        )
        assert any("judgement" in a for a in payload["assumptions"])


class TestMethodologyIsTheConfig:
    """The whole value of this tool is that it cannot drift from the engine."""

    def test_weights_match_the_config_exactly(self, nri: dict) -> None:
        described = describe_methodology(offline_deps(nri), None)
        config = load_weights()
        for hazard, entry in described["hazards"].items():
            expected = {k: float(v) for k, v in config["hazards"][hazard]["weights"].items()}
            assert entry["weights"] == expected

    def test_band_boundaries_are_reported_and_inclusive_lower(self, nri: dict) -> None:
        described = describe_methodology(offline_deps(nri), None)
        assert len(described["bands"]) == len(load_weights()["bands"])
        assert described["bands"][-1]["scores"].endswith("100")
        assert "exclusive upper" in described["band_boundaries"]

    def test_it_states_that_the_numbers_are_not_fitted(self, nri: dict) -> None:
        """The disclosure that matters most, and the one that only existed as a
        comment in weights.yaml before this tool."""
        described = describe_methodology(offline_deps(nri), None)
        assert "NOT MEASURED COEFFICIENTS" in described["status_of_these_numbers"]
        assert "not a prediction" in described["what_the_score_is_not"]

    def test_a_hazard_with_no_historical_component_explains_itself(self, nri: dict) -> None:
        described = describe_methodology(offline_deps(nri), None)
        hurricane = described["hazards"]["hurricane"]
        assert hurricane["historical"] is None
        assert hurricane["historical_excluded_because"]

    def test_an_unknown_hazard_is_refused(self, nri: dict) -> None:
        assert "error" in describe_methodology(offline_deps(nri), "wildfire")


class TestTiesAreVisible:
    def test_an_adjacent_tie_is_flagged(self, nri: dict) -> None:
        """Houston and Miami both sit at FEMA's maximum hurricane index, so this
        tie is in the assignment's own headline question -- and 'the top three'
        would otherwise be cut alphabetically with nothing saying so."""
        ranking = rank_hubs_by_risk(offline_deps(nri), "hurricane")["ranking"]
        tied = [row for row in ranking if row.get("tied_with_next")]
        assert tied, "no tie flagged in a ranking that contains tied scores"
        for row in tied:
            assert row["gap_to_next"] == 0.0

    def test_no_tie_is_claimed_where_scores_differ(self, nri: dict) -> None:
        ranking = rank_hubs_by_risk(offline_deps(nri), "hurricane")["ranking"]
        for row in ranking:
            if row.get("gap_to_next", 1.0) != 0.0:
                assert "tied_with_next" not in row


class TestProvenanceTracksTheData:
    def test_the_source_label_is_derived_from_the_snapshot(self) -> None:
        window = climatology.window()
        label = climatology.historical_source()
        assert window["start"] in label and window["end"] in label

    def test_no_hardcoded_window_survives_in_the_agent_boundary(self) -> None:
        """The label said 2021-2025 while the record ran nine months further."""
        for path in pathlib.Path("app/agent").glob("*.py"):
            assert "2021-2025" not in path.read_text(encoding="utf-8"), path


class TestPortfolioAssembly:
    """What must hold for an investment answer without the model cooperating."""

    def _draft(self, **kwargs: object) -> AgentDraft:
        return AgentDraft(
            interpretation_of_question="which hubs to invest in",
            intent="portfolio",
            answer="...",
            **kwargs,  # type: ignore[arg-type]
        )

    def test_a_portfolio_draft_produces_assessments_at_all(self, nri: dict) -> None:
        """THE SILENT FAILURE THIS GUARDS. A portfolio draft names no hazard, so
        before the branch existed it fell through the intent gate and produced
        zero assessments -- which passes score-integrity and disclosure
        vacuously, renders nothing, and leaves every number in the prose
        ungrounded, because the allowed set is built from assessments."""
        response = assemble(self._draft(), offline_deps(nri))
        assert len(response.assessments) == 40

    def test_the_shortlist_is_the_engines_not_the_models_pick_from_it(
        self, nri: dict
    ) -> None:
        """OBSERVED, NOT HYPOTHETICAL. Asked for "the top three", the model
        filled hub_ids with three hubs even though the schema says to leave it
        empty for a portfolio. Honouring that would re-rank the portfolio over
        just those three: the ORDER would still be the engine's, but WHICH hubs
        reached the table would be the model's -- and a cherry-picked subset in
        the right relative order passes every ordering check there is."""
        response = assemble(
            self._draft(hub_ids=["boston-ma", "new-york-ny", "houston-tx"]),
            offline_deps(nri),
        )
        assert len(response.assessments) == 40

    def test_a_region_filter_is_honoured_because_it_narrows_the_question(
        self, nri: dict
    ) -> None:
        response = assemble(self._draft(region="Midwest"), offline_deps(nri))
        assert len(response.assessments) == 10
        assert {a.region for a in response.assessments} == {"Midwest"}

    def test_every_row_carries_the_investment_reading(self, nri: dict) -> None:
        response = assemble(self._draft(), offline_deps(nri))
        for assessment in response.assessments:
            assert assessment.rank is not None
            # Either tiered, or explicitly not tierable -- never silently absent.
            assert assessment.investability in ("Invest", "Watch", "Low", None)
            if assessment.investability is not None:
                assert assessment.structural_score is not None

    def test_no_cross_hazard_gap_is_ever_published(self, nri: dict) -> None:
        """The rows span hazards, so a pre-computed difference between two of
        them would manufacture the comparison the portfolio refuses to make."""
        response = assemble(self._draft(), offline_deps(nri))
        assert all(a.gap_to_leader is None for a in response.assessments)
        assert all(a.gap_to_next is None for a in response.assessments)
        # ...while a per-hazard ranking still supplies them.
        ranking = assemble(
            AgentDraft(
                interpretation_of_question="rank winter",
                intent="rank",
                answer="...",
                hazards=["winter"],
            ),
            offline_deps(nri),
        )
        assert all(a.gap_to_leader is not None for a in ranking.assessments)

    def test_the_tiers_arrive_grouped_and_in_order(self, nri: dict) -> None:
        response = assemble(self._draft(), offline_deps(nri))
        rank_of = {"Invest": 0, "Watch": 1, "Low": 2, None: 3}
        tiers = [
            rank_of[a.investability]
            for a in sorted(response.assessments, key=lambda a: a.rank or 0)
        ]
        assert tiers == sorted(tiers)

    def test_it_discloses_that_the_tier_is_not_todays_risk(self, nri: dict) -> None:
        """Without this sentence the first analyst to see a high-scoring hub
        tiered Low files a defect."""
        response = assemble(self._draft(), offline_deps(nri))
        notes = " ".join(response.assumptions)
        assert "not by today's risk score" in notes
        assert "not an absolute level of risk" in notes

    def test_all_three_data_sources_are_attributed(self, nri: dict) -> None:
        """A portfolio reads every hazard, so it reads the climatology too --
        even when the model made no historical tool call."""
        response = assemble(self._draft(), offline_deps(nri))
        assert any("FEMA" in s for s in response.sources)
        assert any("NWS" in s for s in response.sources)
        assert any("Open-Meteo" in s for s in response.sources)


class TestEveryFigureTheModelSeesIsAuditable:
    """A number shown to the model must be a number the response carries, or
    `check_groundedness` fails a correct answer -- and a reader has nothing to
    check it against."""

    def test_explain_shows_a_structural_score_the_response_also_carries(
        self, nri: dict
    ) -> None:
        """REGRESSION ON A REAL EVAL FAILURE. `explain_hub_risk` exposes the
        structural score, and the explain assembly path did not carry it, so the
        agent quoted Dallas's 99.7 with nothing to audit it against."""
        from app.agent.tools import explain_hub_risk

        deps = offline_deps(nri)
        payload = explain_hub_risk(deps, "dallas-tx", "flood")
        assert payload["structural_score"] is not None

        response = assemble(
            AgentDraft(
                interpretation_of_question="why is Dallas high",
                intent="explain",
                answer="...",
                hub_ids=["dallas-tx"],
                hazards=["flood"],
            ),
            offline_deps(nri),
        )
        carried = {a.structural_score for a in response.assessments}
        assert payload["structural_score"] in carried

    def test_a_risk_ranking_carries_no_investment_tier(self, nri: dict) -> None:
        """The other half of the separation: a ranking must not be readable as a
        shortlist."""
        response = assemble(
            AgentDraft(
                interpretation_of_question="rank winter",
                intent="rank",
                answer="...",
                hazards=["winter"],
            ),
            offline_deps(nri),
        )
        assert all(a.investability is None for a in response.assessments)
        assert all(a.structural_score is not None for a in response.assessments)


def _alert(event: str, severity: str = "Severe") -> Any:
    """A normalised NWS alert, built here so the test does not depend on the
    upstream feed carrying a fog advisory today."""
    from app.tools.nws import Alert

    return Alert(
        event=event, severity=severity, urgency="Expected", certainty="Likely",
        headline="", onset=None, expires=None, matched_on="zone", alert_id="x",
    )


class TestAlertsSayWhichScoreTheyMove:
    """The NWS issues alerts for far more than the three hazards modelled here,
    and the feed is fetched per HUB. So an unlabelled alert list routinely
    implies a driver that contributes nothing."""

    def test_an_unmodelled_alert_is_labelled_not_scored(self, nri: dict) -> None:
        """REGRESSION ON AN OBSERVED FAILURE. Asked to compare two hubs on
        current conditions after a winter ranking, the agent answered with a
        Dense Fog Advisory. True, and dangerous for road freight -- but it
        contributes 0.0 to the winter score, and nothing said so."""
        from app.scoring.engine import hazards_scored_by

        assert hazards_scored_by("Dense Fog Advisory") == ()
        assert hazards_scored_by("Special Weather Statement") == ()

    def test_a_modelled_alert_names_its_hazard(self) -> None:
        from app.scoring.engine import hazards_scored_by

        assert hazards_scored_by("Winter Storm Warning") == ("winter",)
        assert hazards_scored_by("Flood Warning") == ("flood",)
        assert hazards_scored_by("High Wind Warning") == ("hurricane",)

    def test_every_configured_alert_event_resolves_to_its_hazard(self) -> None:
        """The index is built from weights.yaml, so it cannot drift from the
        events the engine actually scores."""
        from app.scoring.engine import hazards_scored_by, load_weights

        for hazard, config in load_weights()["hazards"].items():
            for event in config["alert_events"]:
                assert hazard in hazards_scored_by(event), f"{event} -> {hazard}"

    def test_the_tool_marks_unscored_alerts_and_keeps_them(self, nri: dict) -> None:
        """Labelled, not filtered. Hiding a live fog advisory to keep the score
        tidy is the opposite of the disclosure discipline everywhere else."""
        from app.agent.tools import get_hub_alerts

        deps = offline_deps(nri)
        deps.alerts = {
            "chicago-il": (
                _alert("Dense Fog Advisory", "Moderate"),
                _alert("Winter Storm Warning", "Severe"),
            )
        }
        payload = get_hub_alerts(deps, "chicago-il")

        assert payload["alert_count"] == 2, "an unscored alert must not be dropped"
        fog = next(a for a in payload["alerts"] if a["event"] == "Dense Fog Advisory")
        snow = next(a for a in payload["alerts"] if a["event"] == "Winter Storm Warning")
        assert fog["scored_for_hazards"] == []
        assert "not_scored" in fog and "moves no risk score" in fog["not_scored"]
        assert snow["scored_for_hazards"] == ["winter"]
        assert "not_scored" not in snow
        assert "contribute to no score" in payload["note_for_agent"]

    def test_assessment_rows_say_which_hazard_an_alert_belongs_to(
        self, nri: dict
    ) -> None:
        from app.agent.runner import _label_alerts

        lines = _label_alerts(
            (
                _alert("Dense Fog Advisory"),
                _alert("Flood Warning"),
                _alert("Blizzard Warning"),
            ),
            "winter",
        )
        assert "not scored, operational context only" in lines[0]
        assert "scores flood, not winter" in lines[1]
        assert lines[2] == "Blizzard Warning (Severe)"
