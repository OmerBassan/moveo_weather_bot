"""Regenerate the golden score baseline.

    python -m scripts.regen_score_baseline            # write tests/data/score_baseline.json
    python -m scripts.regen_score_baseline --check    # exit 1 if it would change, write nothing

WHY A GOLDEN FILE, AND WHY IT IS CHEAP.

All 120 hub-hazard scores are a pure function of two frozen snapshots when no
alerts and no forecast are supplied: `rank_hubs(hubs, hazard, nri)` leaves
`alerts=()` and `forecasts_by_hub={}`, so `_current_component` scores every hub
0.0 -- present, deterministic, and requiring no network. That makes a complete
baseline cheap enough to assert on every test run, with no mocking.

WHY PER HUB-HAZARD AND NOT A HASH.

A single hash over the 120 scores would be fewer lines and would tell you
nothing. The only thing worth knowing when this file moves is WHICH hub and
WHICH hazard moved, and by how much, so the baseline is keyed per pair and
`tests/test_score_baseline.py` reports the moved keys old -> new.

WHY THE COMPONENTS ARE PINNED, NOT JUST THE TOTALS.

A refactor that changed which components are present while leaving the
arithmetic intact would otherwise pass. And for the eight hubs FEMA does not
model hurricanes for, the distinction between `value: null` (not measured) and
`value: 0.0` (measured as zero) is the entire renormalisation argument -- so it
is pinned explicitly rather than left to the total to imply.

This file moving is not automatically a bug. It is a signal that a change moved
a score, which is either the point of the change or a regression. Run without
--check to accept the new numbers, and the diff is the review.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from app.config import ROOT, load_config
from app.hubs import load_hubs
from app.scoring.engine import HAZARDS, rank_hubs

BASELINE_PATH = ROOT / "tests" / "data" / "score_baseline.json"


def build_baseline() -> dict[str, dict[str, Any]]:
    """Score every hub for every hazard with no live inputs.

    Imported by `tests/test_score_baseline.py` so the assertion and the
    regeneration cannot drift apart.
    """
    config = load_config()
    nri = json.loads(config.nri_snapshot_path.read_text(encoding="utf-8"))
    hubs = load_hubs().hubs

    entries: dict[str, dict[str, Any]] = {}
    for hazard in HAZARDS:
        for result in rank_hubs(hubs, hazard, nri):
            entries[f"{result.hub_id}:{hazard}"] = {
                "score": result.score,
                "band": result.band,
                # The investment reading. Pinned alongside the score because a
                # tier is as load-bearing as a number once a shortlist is built
                # from it, and because it is derived -- a change to the band
                # boundaries moves it silently otherwise.
                "structural_score": result.structural_score,
                "transient_score": result.transient_score,
                "investability": result.investability,
                "components": [
                    {
                        "name": component.name,
                        # null is preserved: "not measured" is not zero.
                        "value": (
                            None if component.value is None else round(component.value, 4)
                        ),
                        "weight": round(component.weight, 4),
                    }
                    for component in result.components
                ],
            }
    return {key: entries[key] for key in sorted(entries)}


def render(baseline: dict[str, dict[str, Any]]) -> str:
    return json.dumps(baseline, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate or verify the golden score baseline."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if the baseline would change; write nothing",
    )
    args = parser.parse_args(argv)

    rendered = render(build_baseline())
    existing = (
        BASELINE_PATH.read_text(encoding="utf-8") if BASELINE_PATH.exists() else None
    )

    if args.check:
        if existing == rendered:
            print(f"{BASELINE_PATH.name}: up to date")
            return 0
        print(f"{BASELINE_PATH.name}: WOULD CHANGE -- run without --check to accept")
        return 1

    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(rendered, encoding="utf-8")
    print(
        f"{'wrote' if existing is None else 'updated'} {BASELINE_PATH} "
        f"({len(json.loads(rendered))} hub-hazard scores)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
