"""Resolve each hub to its NWS county and forecast zone codes, once.

    python -m scripts.fetch_zones

WHY THIS EXISTS: MATCHING ALERTS ON THE COUNTY CODE ALONE LOSES ALERTS.
NWS issues some products against counties (UGC `XXCnnn`, with a SAME code) and
others against forecast zones (UGC `XXZnnn`). A zone-issued Winter Storm
Warning need not carry the county code for the hub it covers, so a matcher
that only reads `geocode.SAME` can return "no active alerts" for a hub that is
under a warning right now. That failure is silent and reads as LOW RISK, which
is the worst direction for this system to be wrong in.

So each hub is resolved once, via `/points/{lat},{lon}`, to both its county
UGC and its forecast zone UGC. At query time a single `/alerts/active` call
covers the whole country and each alert is matched against either code.

This is a build step for the same reason as the other two snapshots: a hub's
zone assignment is a property of geography, not of today's weather.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from app import config as cfg
from app.config import AppConfig, load_config
from app.http_client import UpstreamError, build_client, get_json
from app.hubs import Hub, load_hubs

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("fetch_zones")


def _ugc_from_url(url: str | None) -> str | None:
    """`/zones/county/CTC003` -> `CTC003`. The points response gives these as
    URLs, and the alert feed gives bare codes, so one of them has to be
    converted; doing it here means the runtime matcher compares like with like."""
    if not url:
        return None
    code = url.rstrip("/").rsplit("/", 1)[-1]
    return code or None


def resolve(hub: Hub, config: AppConfig, client: Any) -> dict[str, Any]:
    url = f"{cfg.NWS_API_ROOT}/points/{hub.latitude},{hub.longitude}"
    payload = get_json(client, url, {}, max_retries=config.http_max_retries)
    properties = payload.get("properties")
    if not isinstance(properties, dict):
        raise UpstreamError(url, f"no properties block for {hub.id}")

    county_ugc = _ugc_from_url(properties.get("county"))
    zone_ugc = _ugc_from_url(properties.get("forecastZone"))
    fire_ugc = _ugc_from_url(properties.get("fireWeatherZone"))

    if not county_ugc or not zone_ugc:
        raise UpstreamError(
            url,
            f"{hub.id} resolved to county={county_ugc!r} zone={zone_ugc!r} -- "
            "both are required for alert matching",
        )

    return {
        "hub_id": hub.id,
        "county_ugc": county_ugc,
        "forecast_zone_ugc": zone_ugc,
        "fire_weather_zone_ugc": fire_ugc,
        "grid_id": properties.get("gridId"),
        "grid_x": properties.get("gridX"),
        "grid_y": properties.get("gridY"),
        # The endpoint that serves this hub's short-term forecast at runtime.
        # Resolving it now means the live path is one call, not two.
        "forecast_url": properties.get("forecast"),
        "forecast_hourly_url": properties.get("forecastHourly"),
    }


def main() -> int:
    config = load_config()
    registry = load_hubs()
    zones: dict[str, Any] = {}
    mismatches: list[str] = []

    with build_client(config) as client:
        for hub in registry.hubs:
            try:
                resolved = resolve(hub, config, client)
            except UpstreamError as exc:
                logger.error("zone lookup failed for %s: %s", hub.id, exc)
                return 1

            # The county UGC's last three digits are the county FIPS' last
            # three. Checking it here is what makes the SAME-code join in the
            # runtime matcher trustworthy: if NWS disagrees with the registry
            # about which county a hub sits in, this is where it surfaces --
            # not as an alert that quietly never matches.
            derived = resolved["county_ugc"][3:]
            if derived != hub.nws_county_fips[2:]:
                mismatches.append(
                    f"{hub.id}: registry says county {hub.nws_county_fips}, "
                    f"NWS points says {resolved['county_ugc']}"
                )
            zones[hub.id] = resolved
            logger.info(
                "%-18s county=%s zone=%s", hub.id, resolved["county_ugc"], resolved["forecast_zone_ugc"]
            )

    if mismatches:
        raise SystemExit(
            "NWS disagrees with data/hubs.json about these hubs' counties:\n  "
            + "\n  ".join(mismatches)
        )

    document = {
        "source": "NWS API /points/{lat},{lon}",
        "service_url": f"{cfg.NWS_API_ROOT}/points",
        "note": (
            "Alerts are matched against county_ugc OR forecast_zone_ugc: NWS issues "
            "some products by county and others by forecast zone, and matching only "
            "one loses the other."
        ),
        "hubs": zones,
    }
    path = config.hubs_path.parent / "hub_zones.json"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    logger.info("wrote %d hub zone records to %s", len(zones), path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
