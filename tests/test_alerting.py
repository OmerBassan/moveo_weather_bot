"""Tests for risk-change detection.

All synthetic: the diff is pure, so none of this needs the network. The one
property that does need live data -- that two runs in calm weather produce
identical scores -- is demonstrated by running the script twice, and is a
consequence of the engine being deterministic rather than something this
module implements.
"""

from __future__ import annotations

import json

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


class TestTheAgentToolIsReadOnly:
    """`what_changed` is reachable by the model, so its inability to advance the
    baseline has to be a property of the code and not of the prompt."""

    def test_it_never_writes_the_baseline(self, tmp_path, monkeypatch) -> None:
        """THE FAILURE THIS PREVENTS. `save` is what makes a diff 'seen'. If the
        tool advanced the baseline, a user asking "what changed?" twice would be
        told "nothing" the second time, and the change they were reading about
        would be gone from the record."""
        from app import alerting
        from app.agent.tools import Deps, what_changed
        from app.hubs import load_hubs

        state = tmp_path / "risk_state.json"
        before = snapshot("t0", denver_winter=(70.0, "High"))
        state.write_text(json.dumps(before.as_dict()), encoding="utf-8")

        monkeypatch.setattr(alerting, "state_path", lambda config=None: state)
        written: list[object] = []
        monkeypatch.setattr(
            alerting, "save", lambda *a, **k: written.append(a)
        )
        monkeypatch.setattr(
            alerting,
            "take_snapshot",
            lambda *a, **k: snapshot("t1", denver_winter=(85.0, "Very High")),
        )

        deps = Deps(nri={}, registry=load_hubs())
        deps.forecasts = {hub.id: {} for hub in deps.registry.hubs}
        payload = what_changed(deps)

        assert payload["change_count"] == 1
        assert payload["band_crossings"] == 1
        assert written == [], "the agent tool advanced the baseline"
        # ...and the file on disk is untouched.
        assert json.loads(state.read_text(encoding="utf-8")) == before.as_dict()

    def test_it_exposes_no_commit_parameter(self) -> None:
        """A flag the model can set is a flag the model can set wrongly."""
        import inspect

        from app.agent.tools import what_changed

        assert "commit" not in inspect.signature(what_changed).parameters

    def test_a_missing_baseline_is_not_reported_as_no_change(
        self, monkeypatch
    ) -> None:
        from app import alerting
        from app.agent.tools import Deps, what_changed
        from app.hubs import load_hubs

        monkeypatch.setattr(alerting, "load_previous", lambda *a, **k: None)
        payload = what_changed(Deps(nri={}, registry=load_hubs()))
        assert "no_baseline" in payload
        assert "change_count" not in payload

    def test_it_says_a_change_can_never_be_structural(self, monkeypatch) -> None:
        """The reading that matters for an investment answer: a diff is always
        live weather, so it cannot move a tier."""
        from app import alerting
        from app.agent.tools import Deps, what_changed
        from app.hubs import load_hubs

        monkeypatch.setattr(
            alerting, "load_previous",
            lambda *a, **k: snapshot("t0", denver_winter=(70.0, "High")),
        )
        monkeypatch.setattr(
            alerting, "take_snapshot",
            lambda *a, **k: snapshot("t1", denver_winter=(85.0, "Very High")),
        )
        deps = Deps(nri={}, registry=load_hubs())
        deps.forecasts = {hub.id: {} for hub in deps.registry.hubs}
        notes = " ".join(what_changed(deps)["assumptions"])
        assert "investment tier" in notes
        assert "frozen snapshots" in notes
