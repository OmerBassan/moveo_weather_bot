"""Characterization: all 120 hub-hazard scores, pinned to a committed file.

This is not a correctness test. It asserts nothing about whether a score is
RIGHT -- `test_scoring.py` does that. It asserts that scores do not move
UNINTENTIONALLY, which is a different and, during a refactor, more urgent
property.

WHY THIS EXISTS.

`app/alerting.py` fires a change notification on any score delta >= 3.0 or any
band crossing, and then advances `data/risk_state.json` so the second run looks
clean. A refactor that shifts one score by a point therefore does not fail a
test -- it pages someone once and then hides. The engine's arithmetic lives in a
single expression that every one of the 120 numbers flows through, so anything
touching it needs a net under it.

HOW TO READ A FAILURE.

The failure names each hub-hazard pair that moved and shows old -> new. If the
move was the point of your change, accept it:

    python -m scripts.regen_score_baseline

and the diff on `tests/data/score_baseline.json` is the review. If you did not
expect a move, you have found the thing this test exists to find.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from scripts.regen_score_baseline import BASELINE_PATH, build_baseline, render


@pytest.fixture(scope="module")
def committed() -> dict[str, Any]:
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def computed() -> dict[str, Any]:
    return build_baseline()


def _summarise(key: str, before: dict[str, Any], after: dict[str, Any]) -> str:
    """One line per moved pair, leading with the number a reader cares about."""
    if before["score"] != after["score"]:
        return (
            f"  {key}: score {before['score']} -> {after['score']} "
            f"(band {before['band']} -> {after['band']})"
        )
    if before["band"] != after["band"]:
        return f"  {key}: score unchanged at {after['score']}, band {before['band']} -> {after['band']}"
    return f"  {key}: components changed\n      was {before['components']}\n      now {after['components']}"


class TestScoresHaveNotMoved:
    def test_every_hub_hazard_pair_is_still_present(
        self, committed: dict[str, Any], computed: dict[str, Any]
    ) -> None:
        """A hub or hazard silently leaving the baseline is as much a
        regression as a score changing."""
        missing = sorted(set(committed) - set(computed))
        added = sorted(set(computed) - set(committed))
        assert not missing, f"no longer scored: {missing}"
        assert not added, f"newly scored (regenerate the baseline to accept): {added}"

    def test_no_score_has_changed(
        self, committed: dict[str, Any], computed: dict[str, Any]
    ) -> None:
        moved = [
            _summarise(key, committed[key], computed[key])
            for key in sorted(committed)
            if key in computed and committed[key] != computed[key]
        ]
        assert not moved, (
            f"{len(moved)} of {len(committed)} hub-hazard scores moved:\n"
            + "\n".join(moved)
            + "\n\nIf this was intended: python -m scripts.regen_score_baseline"
        )

    def test_the_committed_file_is_exactly_what_the_script_writes(
        self, computed: dict[str, Any]
    ) -> None:
        """Keeps `--check` and this test in agreement. Without it, a
        hand-edited or differently-formatted baseline could satisfy one and
        fail the other."""
        assert BASELINE_PATH.read_text(encoding="utf-8") == render(computed)


class TestTheBaselinePinsTheRenormalisationCases:
    """The cases the engine's renormalisation docstring argues about, asserted
    over the whole network rather than one hub."""

    def test_hurricane_has_exactly_two_components_for_every_hub(
        self, committed: dict[str, Any]
    ) -> None:
        """Hurricane's missing historical component is a methodology decision,
        not missing data (see weights.yaml). `test_scoring.py` pins this for
        one hub; the baseline pins it for all forty, so a config edit that
        reintroduced the component could not pass unnoticed."""
        for key, entry in committed.items():
            if key.endswith(":hurricane"):
                names = [c["name"] for c in entry["components"]]
                assert names == ["baseline", "current"], f"{key} has {names}"

    def test_unmodelled_baselines_are_null_and_never_zero(
        self, committed: dict[str, Any]
    ) -> None:
        """FEMA does not model hurricanes for eight of these counties. `null`
        means not measured; `0.0` would assert no exposure. Conflating them is
        the specific mistake the engine renormalises to avoid."""
        null_baselines = sorted(
            key
            for key, entry in committed.items()
            if any(c["name"] == "baseline" and c["value"] is None for c in entry["components"])
        )
        assert null_baselines == [
            "boise-id:hurricane",
            "denver-co:hurricane",
            "minneapolis-mn:hurricane",
            "portland-or:hurricane",
            "reno-nv:hurricane",
            "sacramento-ca:hurricane",
            "salt-lake-city-ut:hurricane",
            "seattle-wa:hurricane",
        ]

    def test_a_hub_with_no_structural_input_scores_zero_without_live_weather(
        self, committed: dict[str, Any]
    ) -> None:
        """The pathology worth naming: with no FEMA hurricane baseline and no
        historical component, these hubs' entire hurricane score is the current
        component. Quiet weather scores them 0.0; a single Severe alert scores
        them 75.0. Nothing structural moves in between."""
        for key in ("boise-id:hurricane", "reno-nv:hurricane"):
            assert committed[key]["score"] == 0.0
            assert committed[key]["band"] == "Very Low"
