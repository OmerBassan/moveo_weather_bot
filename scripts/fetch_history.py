"""Fetch the five-year daily climatology for every hub, once, and freeze it.

    python -m scripts.fetch_history

Like the NRI snapshot this is a build step: a five-year climatology cannot
change between user questions, and fetching it live would put a network
dependency on the demo path for a number that is fixed.

BATCHING IS NOT FREE, WHATEVER THE DOCUMENTATION SAYS. Open-Meteo's terms
describe a limit of 600 calls per minute and say a multi-coordinate request
counts as one call. Observed behaviour disagrees: a single request for 20
coordinates x 1,826 days x 4 variables returns

    429 {"reason": "Minutely API request limit exceeded..."}

so a request is evidently weighted by locations x days x variables rather
than counted as one. Hence small chunks and a pause between them. The
measured working point is 5 coordinates per request; the whole run therefore
takes minutes, which is the right trade for a file that is written once.

AND THE RUN IS RESUMABLE. Losing four successful chunks to a 429 on the
fifth is how a one-off build step turns into a twenty-minute fight. Each
chunk is written to the snapshot as it lands, and a re-run fetches only the
hubs that are missing.

UNITS ARE NORMALISED HERE, AT THE BOUNDARY. Open-Meteo answers in cm, mm, degC
and km/h. Everything downstream -- scoring, prompts, UI -- sees inches and
mph, and the snapshot records the source unit alongside so the conversion is
auditable rather than assumed.

PROVENANCE. The API resolves each coordinate to the nearest 9 km reanalysis
grid cell, so what comes back is not the point that was asked for. Both the
requested and the returned coordinates are stored, plus the cell's elevation.
That turns "the resolution caveat" from boilerplate into something a reviewer
can check per hub -- and scripts/verify_snapshots.py prints the distance.

THE MODEL AND THE DAY BOUNDARY ARE PINNED, not defaulted. See app/config.py:
the default is a blend whose seam moves with the window, and a UTC day
boundary puts a storm on the wrong calendar day for a US hub.
"""

from __future__ import annotations

import json
import logging
import math
import sys
import time
from typing import Any

from app import config as cfg
from app.config import AppConfig, load_config
from app.http_client import UpstreamError, build_client, get_json_list
from app.hubs import Hub, HubRegistry, load_hubs

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("fetch_history")

# Coordinates per request, and the pause between requests. Both are empirical:
# 20 coordinates over this window reproducibly returns 429, 5 does not. The
# minutely bucket is documented to clear "in one minute", so the pause is a
# minute plus a margin.
CHUNK_SIZE = 5
PAUSE_BETWEEN_CHUNKS_SECONDS = 65

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """How far the returned grid cell sits from the hub we asked about."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _convert(values: list[Any], factor: float) -> list[float | None]:
    """Nulls survive conversion as nulls. A missing day is missing, not zero --
    the same rule the NRI snapshot follows, and for the same reason."""
    return [None if v is None else round(v * factor, 4) for v in values]


def fetch_chunk(hubs: tuple[Hub, ...], config: AppConfig) -> list[dict[str, Any]]:
    params = {
        "latitude": ",".join(str(h.latitude) for h in hubs),
        "longitude": ",".join(str(h.longitude) for h in hubs),
        "start_date": config.history_start_date,
        "end_date": config.history_end_date,
        "daily": ",".join(cfg.OPEN_METEO_DAILY_VARS),
        # Both pinned rather than defaulted -- see app/config.py for why the
        # default "best match" blend is not reproducible.
        "models": cfg.OPEN_METEO_MODEL,
        "timezone": cfg.OPEN_METEO_TIMEZONE,
    }
    with build_client(config) as client:
        locations = get_json_list(
            client,
            cfg.OPEN_METEO_ARCHIVE_URL,
            params,
            max_retries=config.http_max_retries,
        )

    # Open-Meteo returns locations in request order and does not echo an
    # identifier we can join on, so position IS the join. Asserting the count
    # is what makes that safe: a short response would otherwise shift every
    # subsequent hub's weather onto the wrong city, silently.
    if len(locations) != len(hubs):
        raise UpstreamError(
            cfg.OPEN_METEO_ARCHIVE_URL,
            f"asked for {len(hubs)} locations and got {len(locations)} back -- "
            "results are matched by position, so a mismatch cannot be reconciled",
        )
    return locations


def normalise(hub: Hub, location: dict[str, Any]) -> dict[str, Any]:
    daily = location.get("daily")
    if not isinstance(daily, dict) or "time" not in daily:
        raise UpstreamError(
            cfg.OPEN_METEO_ARCHIVE_URL, f"no daily block returned for {hub.id}"
        )

    dates = daily["time"]
    for variable in cfg.OPEN_METEO_DAILY_VARS:
        series = daily.get(variable)
        if series is None:
            raise UpstreamError(
                cfg.OPEN_METEO_ARCHIVE_URL, f"{hub.id} is missing variable {variable!r}"
            )
        if len(series) != len(dates):
            raise UpstreamError(
                cfg.OPEN_METEO_ARCHIVE_URL,
                f"{hub.id} variable {variable!r} has {len(series)} values "
                f"for {len(dates)} dates",
            )

    returned_lat = location["latitude"]
    returned_lon = location["longitude"]

    return {
        "hub_id": hub.id,
        "dates": dates,
        "daily": {
            "snowfall_in": _convert(daily["snowfall_sum"], cfg.CM_TO_INCH),
            "precipitation_in": _convert(daily["precipitation_sum"], cfg.MM_TO_INCH),
            # Celsius is not a scale factor, so it is converted explicitly.
            "temp_min_f": [
                None if v is None else round(v * 9 / 5 + 32, 2)
                for v in daily["temperature_2m_min"]
            ],
            "wind_gust_max_mph": _convert(daily["wind_gusts_10m_max"], cfg.KMH_TO_MPH),
        },
        "provenance": {
            "requested_latitude": hub.latitude,
            "requested_longitude": hub.longitude,
            "returned_latitude": returned_lat,
            "returned_longitude": returned_lon,
            "grid_cell_offset_km": round(
                haversine_km(hub.latitude, hub.longitude, returned_lat, returned_lon), 2
            ),
            "elevation_m": location.get("elevation"),
            "timezone": location.get("timezone"),
            "source_units": location.get("daily_units"),
        },
    }


def _load_existing(config: AppConfig) -> dict[str, Any]:
    """Whatever a previous run already fetched, keyed by hub id.

    A snapshot written for a different window is discarded rather than topped
    up: mixing hubs measured over 2021-2025 with hubs measured over some other
    span would produce a ranking whose hubs are not comparable, and nothing
    downstream could detect it.
    """
    path = config.history_snapshot_path
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    window = document.get("window", {})
    if (
        window.get("start") != config.history_start_date
        or window.get("end") != config.history_end_date
    ):
        logger.warning(
            "existing snapshot covers %s..%s but this run wants %s..%s -- refetching all",
            window.get("start"),
            window.get("end"),
            config.history_start_date,
            config.history_end_date,
        )
        return {}
    return document.get("hubs", {})


def _write(config: AppConfig, normalised: dict[str, Any]) -> None:
    document = _document(config, normalised)
    config.history_snapshot_path.write_text(json.dumps(document), encoding="utf-8")


def main() -> int:
    config = load_config()
    registry: HubRegistry = load_hubs()

    normalised = _load_existing(config)
    pending = tuple(h for h in registry.hubs if h.id not in normalised)

    if not pending:
        logger.info("all %d hubs already present in the snapshot", len(registry.hubs))
        return 0
    if normalised:
        logger.info("resuming: %d hub(s) already fetched, %d to go", len(normalised), len(pending))

    logger.info(
        "requesting %s..%s for %d hubs in %d chunk(s) of %d, %ds apart",
        config.history_start_date,
        config.history_end_date,
        len(pending),
        math.ceil(len(pending) / CHUNK_SIZE),
        CHUNK_SIZE,
        PAUSE_BETWEEN_CHUNKS_SECONDS,
    )

    for start in range(0, len(pending), CHUNK_SIZE):
        chunk = pending[start : start + CHUNK_SIZE]
        if start:
            time.sleep(PAUSE_BETWEEN_CHUNKS_SECONDS)
        try:
            locations = fetch_chunk(chunk, config)
            for hub, location in zip(chunk, locations, strict=True):
                normalised[hub.id] = normalise(hub, location)
        except UpstreamError as exc:
            # Persist what did land, then stop. A re-run picks up from here.
            _write(config, normalised)
            logger.error("history fetch failed after %d hub(s): %s", len(normalised), exc)
            logger.error("progress saved -- re-run this script to continue")
            return 1
        _write(config, normalised)
        logger.info("normalised %d/%d hubs", len(normalised), len(registry.hubs))

    logger.info("wrote %d hub histories to %s", len(normalised), config.history_snapshot_path)
    return 0


def _document(config: AppConfig, normalised: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "Open-Meteo Historical Weather API",
        "model": cfg.OPEN_METEO_MODEL,
        "model_resolution_km": 9,
        "service_url": cfg.OPEN_METEO_ARCHIVE_URL,
        "day_boundary": "local time at each hub (timezone=auto)",
        "attribution": cfg.OPEN_METEO_ATTRIBUTION_TEXT,
        "licence": "CC BY 4.0; the free API tier is non-commercial use only",
        "window": {"start": config.history_start_date, "end": config.history_end_date},
        "units": {
            "snowfall_in": "inches",
            "precipitation_in": "inches",
            "temp_min_f": "degrees Fahrenheit",
            "wind_gust_max_mph": "miles per hour",
        },
        "caveat": (
            "Gridded reanalysis, not station observations. Each value represents a "
            "9 km model cell, so a positive snowfall figure means snow fell somewhere "
            "in that cell, not that it accumulated at the hub. ERA5-family products "
            "are documented to spread precipitation over too many days while "
            "under-representing high-intensity events, so day counts at a zero "
            "threshold run high and localised lake-effect and orographic totals are "
            "smoothed away. These values are NOT bias-corrected against station "
            "records, and are not directly comparable to a NOAA station normal: a "
            "station normal answers 'what was measured at this site', a grid cell "
            "answers 'what does the model estimate across this area'."
        ),
        "hubs": normalised,
    }


if __name__ == "__main__":
    sys.exit(main())
