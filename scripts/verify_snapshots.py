"""The gate between the frozen data and everything built on top of it.

    python -m scripts.verify_snapshots

Exits non-zero if any snapshot is unusable, and prints the two tables a human
has to actually look at:

  NULL COVERAGE     which hubs have no FEMA baseline for which hazard. Nulls
                    are EXPECTED (coastal flooding is not modelled inland) and
                    are data, not failures -- but a null in winter weather or
                    inland flooding, which apply nationwide, means something
                    is wrong with the hub rather than with the hazard.
  GRID CELL OFFSET  how far each hub's reanalysis cell sits from the hub. This
                    is the "9-25 km resolution" caveat made checkable: a hub
                    whose cell landed 30 km away across a mountain range is
                    visible here rather than discovered later as a weather
                    series nobody can explain.

Nothing here is a unit test. These are properties of fetched data, which can
change when the upstream releases a new version, so they are checked at the
point the data enters the project rather than asserted in the test suite.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date
from typing import Any

from app.config import AppConfig, load_config
from app.hubs import HubRegistry, load_hubs

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("verify")

# Hazards modelled for every US county. A null here is a broken hub record,
# not an inapplicable hazard.
UNIVERSAL_HAZARDS = ("winter_weather", "inland_flooding")

# Beyond this, the reanalysis cell is far enough from the hub that the series
# may describe materially different terrain. A warning, not an error: the
# right response is a human deciding, not a script refusing.
GRID_OFFSET_WARN_KM = 25.0


def _expected_days(config: AppConfig) -> int:
    start = date.fromisoformat(config.history_start_date)
    end = date.fromisoformat(config.history_end_date)
    return (end - start).days + 1


def check_nri(registry: HubRegistry, config: AppConfig) -> list[str]:
    document = json.loads(config.nri_snapshot_path.read_text(encoding="utf-8"))
    hubs = document["hubs"]
    failures: list[str] = []

    missing = [h.id for h in registry.hubs if h.id not in hubs]
    if missing:
        failures.append(f"NRI snapshot is missing {len(missing)} hub(s): {missing}")
        return failures

    hazards = tuple(next(iter(hubs.values()))["hazards"].keys())
    logger.info("\nFEMA NRI baseline -- null means 'hazard not modelled here', NOT zero risk")
    logger.info("  %-20s %s", "hub", "  ".join(f"{h[:9]:>9}" for h in hazards))

    null_counts = dict.fromkeys(hazards, 0)
    for hub in registry.hubs:
        record = hubs[hub.id]
        cells = []
        for hazard in hazards:
            score = record["hazards"][hazard]["risk_score"]
            if score is None:
                null_counts[hazard] += 1
                cells.append(f"{'--':>9}")
                if hazard in UNIVERSAL_HAZARDS:
                    failures.append(
                        f"{hub.id} has a null {hazard} score, but that hazard is "
                        f"modelled for every county -- check nri_fips {hub.nri_fips}"
                    )
            else:
                cells.append(f"{score:>9.1f}")
        logger.info("  %-20s %s", hub.id, "  ".join(cells))

    logger.info(
        "  %-20s %s",
        "NULLS",
        "  ".join(f"{null_counts[h]:>9}" for h in hazards),
    )
    return failures


def check_history(registry: HubRegistry, config: AppConfig) -> list[str]:
    document = json.loads(config.history_snapshot_path.read_text(encoding="utf-8"))
    hubs = document["hubs"]
    failures: list[str] = []
    expected = _expected_days(config)

    missing = [h.id for h in registry.hubs if h.id not in hubs]
    if missing:
        failures.append(f"history snapshot is missing {len(missing)} hub(s): {missing}")
        return failures

    offsets: list[tuple[float, str, Any]] = []
    for hub in registry.hubs:
        record = hubs[hub.id]
        if len(record["dates"]) != expected:
            failures.append(
                f"{hub.id} has {len(record['dates'])} days, expected {expected} "
                f"for {config.history_start_date}..{config.history_end_date}"
            )
        for variable, series in record["daily"].items():
            if len(series) != len(record["dates"]):
                failures.append(f"{hub.id}.{variable} has {len(series)} values for "
                                f"{len(record['dates'])} dates")
            elif all(v is None for v in series):
                # An all-null variable would make its hazard component silently
                # unscoreable for this hub.
                failures.append(f"{hub.id}.{variable} is entirely null")
        provenance = record["provenance"]
        offsets.append((provenance["grid_cell_offset_km"], hub.id, provenance))

    logger.info("\nOpen-Meteo grid cells -- how far the reanalysis cell sits from the hub")
    logger.info("  %-20s %9s %9s  %s", "hub", "offset km", "elev m", "timezone")
    for offset, hub_id, provenance in sorted(offsets, reverse=True):
        flag = "  <-- beyond cell resolution" if offset > GRID_OFFSET_WARN_KM else ""
        logger.info(
            "  %-20s %9.2f %9s  %s%s",
            hub_id,
            offset,
            provenance.get("elevation_m"),
            provenance.get("timezone"),
            flag,
        )
    return failures


def check_zones(registry: HubRegistry, config: AppConfig) -> list[str]:
    path = config.hubs_path.parent / "hub_zones.json"
    if not path.exists():
        return [f"{path} does not exist -- run scripts.fetch_zones"]

    hubs = json.loads(path.read_text(encoding="utf-8"))["hubs"]
    failures = [
        f"hub_zones is missing {h.id}" for h in registry.hubs if h.id not in hubs
    ]
    diverging = [h.id for h in registry.hubs if h.keys_diverge]
    if diverging:
        logger.info(
            "\nFEMA and NWS use different county geographies for: %s", ", ".join(diverging)
        )
        logger.info("  (handled by the separate nri_fips / nws_county_fips keys)")
    return failures


def main() -> int:
    config = load_config()
    registry = load_hubs()

    logger.info("hub registry: %d hubs", len(registry.hubs))
    by_region: dict[str, int] = {}
    for hub in registry.hubs:
        by_region[hub.region] = by_region.get(hub.region, 0) + 1
    logger.info("  by region: %s", ", ".join(f"{k}={v}" for k, v in sorted(by_region.items())))

    failures: list[str] = []
    failures += check_nri(registry, config)
    failures += check_history(registry, config)
    failures += check_zones(registry, config)

    if failures:
        logger.error("\n%d PROBLEM(S):", len(failures))
        for failure in failures:
            logger.error("  - %s", failure)
        return 1

    logger.info("\nAll snapshots verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
