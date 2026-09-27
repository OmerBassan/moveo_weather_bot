"""Live NWS alerts: the one thing this system fetches at question time.

ONE CALL FOR THE WHOLE COUNTRY. `/alerts/active` returns every active alert in
the US, and each hub is matched against it locally. The alternative -- one
request per hub -- costs 40 requests per question against an upstream whose
rate limit is deliberately undocumented, and gets slower as the network grows.
This way the cost is constant in the number of hubs.

MATCHING IS ON THREE KEYS, NOT ONE. NWS issues some products against counties
(UGC `XXCnnn`, carrying a SAME code) and others against forecast zones (UGC
`XXZnnn`). An alert matched only by county silently loses every zone-issued
warning -- and "no active alerts" reads to a user as LOW RISK rather than as a
missed join. So each alert is matched against the hub's county UGC, its
forecast zone UGC, and its county FIPS via the SAME code.

The normalised alert is small on purpose. Raw NWS features carry multi-kilobyte
`description` and `instruction` blocks; handing those to an LLM wastes context
and invites it to quote forecast prose as if it were a measurement.
"""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from app import config as cfg
from app.config import AppConfig, load_config, same_to_fips
from app.http_client import UpstreamError, build_client, get_json
from app.hubs import Hub, load_hubs

logger = logging.getLogger(__name__)

ALERTS_URL = f"{cfg.NWS_API_ROOT}/alerts/active"


@dataclass(frozen=True)
class Alert:
    """One active alert, normalised. Only fields the engine or a human needs."""

    event: str
    severity: str
    urgency: str
    certainty: str
    headline: str
    onset: str | None
    expires: str | None
    matched_on: str
    alert_id: str

    def as_dict(self) -> dict[str, Any]:
        """The shape the scoring engine consumes."""
        return {
            "event": self.event,
            "severity": self.severity,
            "urgency": self.urgency,
            "certainty": self.certainty,
            "headline": self.headline,
            "expires": self.expires,
        }


@lru_cache(maxsize=1)
def load_zones(path: Path | None = None) -> dict[str, Any]:
    target = path or (load_config().hubs_path.parent / "hub_zones.json")
    return json.loads(target.read_text(encoding="utf-8"))["hubs"]


def _match_keys(hub: Hub) -> dict[str, str]:
    """The three identifiers an alert may carry for this hub."""
    zones = load_zones().get(hub.id, {})
    keys = {}
    if county := zones.get("county_ugc"):
        keys[county] = "county zone"
    if zone := zones.get("forecast_zone_ugc"):
        keys[zone] = "forecast zone"
    keys[hub.nws_county_fips] = "county FIPS"
    return keys


def _normalise(feature: dict[str, Any], matched_on: str) -> Alert | None:
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        return None
    event = properties.get("event")
    if not event:
        return None
    return Alert(
        event=str(event),
        # Absent severity becomes "Unknown" rather than defaulting to something
        # scoreable. The engine treats Unknown as missing, not as zero.
        severity=str(properties.get("severity") or "Unknown"),
        urgency=str(properties.get("urgency") or "Unknown"),
        certainty=str(properties.get("certainty") or "Unknown"),
        headline=str(properties.get("headline") or event),
        onset=properties.get("onset"),
        expires=properties.get("expires"),
        matched_on=matched_on,
        alert_id=str(properties.get("id") or ""),
    )


def fetch_active_alerts(config: AppConfig | None = None) -> dict[str, tuple[Alert, ...]]:
    """Every active US alert, grouped by the hubs it covers.

    Raises `UpstreamError`. The caller decides what a live-data failure means;
    this module will not invent an empty result, because "no alerts" and "we
    could not reach the NWS" must not look the same to the scoring engine.
    """
    config = config or load_config()
    registry = load_hubs()

    with build_client(config) as client:
        payload = get_json(client, ALERTS_URL, {}, max_retries=config.http_max_retries)

    features = payload.get("features")
    if not isinstance(features, list):
        raise UpstreamError(ALERTS_URL, "response contained no 'features' list")

    # Index the hubs by every key an alert might name, once.
    key_to_hubs: dict[str, list[tuple[str, str]]] = {}
    for hub in registry.hubs:
        for key, kind in _match_keys(hub).items():
            key_to_hubs.setdefault(key, []).append((hub.id, kind))

    by_hub: dict[str, list[Alert]] = {hub.id: [] for hub in registry.hubs}
    seen: set[tuple[str, str]] = set()

    for feature in features:
        geocode = (feature.get("properties") or {}).get("geocode") or {}
        candidates: list[str] = []
        candidates.extend(str(u) for u in geocode.get("UGC", []) or [])
        for same in geocode.get("SAME", []) or []:
            try:
                candidates.append(same_to_fips(str(same)))
            except ValueError:
                # A malformed SAME code is the upstream's problem, not a reason
                # to drop the alert: its UGC codes may still match.
                logger.debug("skipping malformed SAME code %r", same)

        for key in candidates:
            for hub_id, kind in key_to_hubs.get(key, []):
                alert = _normalise(feature, kind)
                if alert is None:
                    continue
                # One alert can name both a county and a zone for the same hub.
                fingerprint = (hub_id, alert.alert_id or alert.headline)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                by_hub[hub_id].append(alert)

    total = sum(len(v) for v in by_hub.values())
    logger.info(
        "matched %d hub-alert pair(s) from %d active US alerts", total, len(features)
    )
    return {hub_id: tuple(alerts) for hub_id, alerts in by_hub.items()}


def alerts_as_engine_input(
    alerts_by_hub: dict[str, tuple[Alert, ...]],
) -> dict[str, tuple[dict[str, Any], ...]]:
    return {
        hub_id: tuple(a.as_dict() for a in alerts)
        for hub_id, alerts in alerts_by_hub.items()
    }


def fetched_at() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------- forecast --

# How far ahead the forecast component looks. Three days is the horizon a
# logistics planner can actually act on -- reroute, pre-position, warn a
# customer -- and it is inside the range where NWS quantitative grids stay
# meaningful.
FORECAST_HOURS = 72

# Forecasts are refetched at most this often. NWS updates gridded forecasts on
# roughly an hourly cadence, so a shorter TTL buys nothing and costs 40
# requests. Long enough that a conversation's follow-up questions reuse one
# fetch; short enough that "right now" means it.
FORECAST_TTL_SECONDS = 1800

_forecast_cache: dict[str, tuple[float, "Forecast"]] = {}


@dataclass(frozen=True)
class Forecast:
    """Quantitative near-term forecast for one hub, normalised to US units.

    Built from the NWS gridpoint product, which returns NUMBERS with declared
    units -- not the prose forecast. That distinction is the whole reason a
    forecast can feed a deterministic score at all: turning "chance of snow
    showers, heavy at times" into a number would be inventing a scoring rule
    at runtime, which is exactly what this architecture forbids.
    """

    hub_id: str
    hours: int
    snowfall_in: float
    precipitation_in: float
    max_wind_gust_mph: float
    max_precip_probability: float
    issued_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "window_hours": self.hours,
            "snowfall_in": round(self.snowfall_in, 2),
            "precipitation_in": round(self.precipitation_in, 2),
            "max_wind_gust_mph": round(self.max_wind_gust_mph, 1),
            "max_precip_probability_pct": round(self.max_precip_probability, 0),
        }


def _within_window(valid_time: str, horizon: datetime) -> bool:
    """NWS validTime is an ISO8601 interval, `<start>/<duration>`. Only the
    start is needed: a value beginning after the horizon is outside it."""
    start = valid_time.split("/", 1)[0]
    try:
        return datetime.fromisoformat(start) <= horizon
    except ValueError:
        return False


def _accumulate(block: dict[str, Any] | None, horizon: datetime, factor: float) -> float:
    """Sum a quantitative series over the window."""
    if not block:
        return 0.0
    return sum(
        (entry.get("value") or 0.0) * factor
        for entry in block.get("values", [])
        if _within_window(entry.get("validTime", ""), horizon)
    )


def _peak(block: dict[str, Any] | None, horizon: datetime, factor: float) -> float:
    if not block:
        return 0.0
    values = [
        (entry.get("value") or 0.0) * factor
        for entry in block.get("values", [])
        if _within_window(entry.get("validTime", ""), horizon)
    ]
    return max(values) if values else 0.0


def fetch_forecast(hub: Hub, config: AppConfig | None = None) -> Forecast:
    """One hub's quantitative forecast. Cached for `FORECAST_TTL_SECONDS`.

    Raises `UpstreamError`; the caller decides what a failure means. It must
    never be turned into a zero forecast, which would read as "calm".
    """
    now = time.time()
    if (cached := _forecast_cache.get(hub.id)) and now - cached[0] < FORECAST_TTL_SECONDS:
        return cached[1]

    config = config or load_config()
    zones = load_zones().get(hub.id, {})
    office, grid_x, grid_y = zones.get("grid_id"), zones.get("grid_x"), zones.get("grid_y")
    if not office or grid_x is None or grid_y is None:
        raise UpstreamError(cfg.NWS_API_ROOT, f"no forecast grid recorded for {hub.id}")

    url = f"{cfg.NWS_API_ROOT}/gridpoints/{office}/{grid_x},{grid_y}"
    with build_client(config) as client:
        payload = get_json(client, url, {}, max_retries=config.http_max_retries)

    properties = payload.get("properties")
    if not isinstance(properties, dict):
        raise UpstreamError(url, f"no properties block for {hub.id}")

    horizon = datetime.now(timezone.utc) + timedelta(hours=FORECAST_HOURS)
    forecast = Forecast(
        hub_id=hub.id,
        hours=FORECAST_HOURS,
        # NWS serves these as wmoUnit:mm and wmoUnit:km_h-1 (verified against
        # the live response), normalised here to match the rest of the system.
        snowfall_in=_accumulate(properties.get("snowfallAmount"), horizon, cfg.MM_TO_INCH),
        precipitation_in=_accumulate(
            properties.get("quantitativePrecipitation"), horizon, cfg.MM_TO_INCH
        ),
        max_wind_gust_mph=_peak(properties.get("windGust"), horizon, cfg.KMH_TO_MPH),
        max_precip_probability=_peak(
            properties.get("probabilityOfPrecipitation"), horizon, 1.0
        ),
        issued_at=str(properties.get("updateTime") or ""),
    )
    _forecast_cache[hub.id] = (now, forecast)
    return forecast


def fetch_forecasts(hubs: tuple[Hub, ...], config: AppConfig | None = None) -> dict[str, Forecast]:
    """Forecasts for many hubs, concurrently.

    Sequentially this is ~40 round trips on the critical path of a chat turn.
    A thread pool keeps it near the latency of the slowest single request. The
    pool is small deliberately: NWS does not publish its rate limit and says
    proxies are the ones that hit it, so hammering it with 40 parallel
    connections is how a demo earns a 429.
    """
    config = config or load_config()
    results: dict[str, Forecast] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_forecast, hub, config): hub for hub in hubs}
        for future in as_completed(futures):
            hub = futures[future]
            try:
                results[hub.id] = future.result()
            except UpstreamError as exc:
                # One hub's forecast failing must not fail the ranking. The
                # component renormalises around a missing forecast and says so.
                logger.warning("forecast unavailable for %s: %s", hub.id, exc.detail)
    return results
