"""Tests for risk-change detection.

All synthetic: the diff is pure, so none of this needs the network. The one
property that does need live data -- that two runs in calm weather produce
identical scores -- is demonstrated by running the script twice, and is a
consequence of the engine being deterministic rather than something this
module implements.
"""

from __future__ import annotations

import pytest

from app.alerting import DEFAULT_MIN_DELTA, RiskSnapshot, diff, render, webhook_payload


def snapshot(taken_at: str, **scores: tuple[float, str]) -> RiskSnapshot:
    """`snapshot("t0", miami_flood=(70.0, "High"))` -> a one-entry snapshot."""
    built = RiskSnapshot(taken_at=taken_at)
    for name, (score, band) in scores.items():
        hub_id, hazard = name.rsplit("_", 1)
        built.scores[RiskSnapshot.key(hub_id, hazard)] = {
            "hub_id": hub_id,
            "hub": hub_id.replace("-", " ").title(),
            "hazard": hazard,
            "score": score,
            "band": band,
            "top_driver": "current",
            "components": [],
            "active_alerts": [],
        }
    return built


class TestThreshold:
    def test_a_small_move_within_a_band_is_not_reported(self) -> None:
        """Forecast values shift hour to hour. Without a threshold this would
        page someone about weather that has not arrived."""
        before = snapshot("t0", miami_flood=(70.0, "High"))
        after = snapshot("t1", miami_flood=(71.0, "High"))
        assert diff(before, after) == []

    def test_a_move_at_the_threshold_is_reported(self) -> None:
        before = snapshot("t0", miami_flood=(70.0, "High"))
        after = snapshot("t1", miami_flood=(70.0 + DEFAULT_MIN_DELTA, "High"))
        assert len(diff(before, after)) == 1

    def test_a_decrease_is_reported_too(self) -> None:
        """Risk falling is also news: it can release a hub from a watch list."""
        before = snapshot("t0", miami_flood=(80.0, "Very High"))
        after = snapshot("t1", miami_flood=(70.0, "High"))
        (change,) = diff(before, after)
        assert change.delta == -10.0
        assert change.direction == "decreased"


class TestBandCrossing:
    def test_a_band_crossing_is_reported_below_the_threshold(self) -> None:
        """Crossing 60.0 is the entire reason a boundary is drawn there. A
        0.2-point move that crosses it matters more than a 2-point move that
        does not, because bands are what an investment conversation uses."""
        before = snapshot("t0", dallas_flood=(59.9, "Moderate"))
        after = snapshot("t1", dallas_flood=(60.1, "High"))
        (change,) = diff(before, after)
        assert change.band_changed
        assert abs(change.delta) < DEFAULT_MIN_DELTA
        assert "Moderate" in change.headline() and "High" in change.headline()

    def test_a_large_move_inside_one_band_is_not_a_crossing(self) -> None:
        before = snapshot("t0", denver_winter=(61.0, "High"))
        after = snapshot("t1", denver_winter=(75.0, "High"))
        (change,) = diff(before, after)
        assert not change.band_changed


class TestNewEntries:
    def test_a_newly_added_hub_is_not_a_risk_change(self) -> None:
        """It has no previous value to have moved from. Reporting it would
        make every registry edit look like a weather event."""
        before = snapshot("t0", miami_flood=(70.0, "High"))
        after = snapshot("t1", miami_flood=(70.0, "High"), boise_winter=(88.0, "Very High"))
        assert diff(before, after) == []

    def test_a_removed_hub_is_not_reported(self) -> None:
        before = snapshot("t0", miami_flood=(70.0, "High"), boise_winter=(88.0, "Very High"))
        after = snapshot("t1", miami_flood=(70.0, "High"))
        assert diff(before, after) == []


class TestOrderingAndOutput:
    def test_largest_absolute_move_comes_first(self) -> None:
        before = snapshot(
            "t0", miami_flood=(70.0, "High"), denver_winter=(50.0, "Moderate"),
            boise_winter=(40.0, "Low"),
        )
        after = snapshot(
            "t1", miami_flood=(75.0, "High"), denver_winter=(70.0, "High"),
            boise_winter=(48.0, "Moderate"),
        )
        assert [c.hub_id for c in diff(before, after)] == ["denver", "boise", "miami"]

    def test_no_changes_renders_a_plain_sentence(self) -> None:
        text = render([], "t0", "t1")
        assert "No risk changes" in text

    def test_webhook_payload_carries_its_own_thresholds(self) -> None:
        """Same disclosure discipline as a chat answer: whatever decided what
        counts as a change travels with the changes."""
        before = snapshot("t0", miami_flood=(70.0, "High"))
        after = snapshot("t1", miami_flood=(80.0, "Very High"))
        payload = webhook_payload(diff(before, after), before, after)

        assert payload["event"] == "weather_risk_changed"
        assert payload["change_count"] == 1
        assert payload["band_crossings"] == 1
        assert payload["assumptions"] and payload["sources"]
        assert any("deterministic" in a for a in payload["assumptions"])

    def test_identical_snapshots_produce_nothing(self) -> None:
        """The property the whole feature rests on: an unchanged score is
        exactly equal, because the engine is deterministic. If a model
        produced the number, every run would differ and every run would look
        like a change."""
        before = snapshot("t0", miami_flood=(73.1, "High"), denver_winter=(61.5, "High"))
        after = snapshot("t1", miami_flood=(73.1, "High"), denver_winter=(61.5, "High"))
        assert diff(before, after) == []
