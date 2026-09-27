"""Fetch the FEMA National Risk Index baseline for every hub, once, and freeze it.

    python -m scripts.fetch_nri

WHY THIS IS A BUILD STEP AND NOT A RUNTIME TOOL. The NRI is a static annual
release (v1.20.0, December 2025). Calling ArcGIS on every user question would
add a network dependency to the demo path in exchange for a number that
cannot change between requests. So it is fetched once and committed, and the
agent's `get_hazard_profile` tool is a dictionary lookup that still carries
full provenance.

This script is also the validator for `data/hubs.json`'s FIPS codes: a code
that returns no row is named and the script exits non-zero. A hub whose FIPS
is a typo would otherwise reach the scoring engine as a hub that silently has
no baseline exposure -- which reads as "safe".

`outFields=*` is deliberate. Requesting named fields would mean hardcoding
field names verified from documentation rather than observed from the
service; asking for everything and projecting locally means no field name is
ever guessed. 40 rows without geometry is a small response.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from app import config as cfg
from app.config import AppConfig, load_config
from app.http_client import UpstreamError, build_client, get_json
from app.hubs import HubRegistry, load_hubs

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("fetch_nri")


def _where_clause(fips_codes: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{code}'" for code in fips_codes)
    return f"{cfg.NRI_FIPS_FIELD} IN ({quoted})"


def fetch_raw(registry: HubRegistry, config: AppConfig) -> dict[str, Any]:
    """One request for all hubs. 40 rows is far below the 2,000 maxRecordCount,
    so there is no pagination path here -- and writing one "just in case" would
    be untested code on a path that never executes."""
    params = {
        "where": _where_clause(registry.nri_fips_codes),
        "outFields": "*",
        "returnGeometry": "false",
        "f": "json",
    }
    with build_client(config) as client:
        payload = get_json(
            client, cfg.NRI_QUERY_URL, params, max_retries=config.http_max_retries
        )

    features = payload.get("features")
    if not isinstance(features, list):
        raise UpstreamError(cfg.NRI_QUERY_URL, "response contained no 'features' list")

    # A silent truncation would look like missing hubs, so check it explicitly.
    if payload.get("exceededTransferLimit"):
        raise UpstreamError(
            cfg.NRI_QUERY_URL,
            "response was truncated at the service record limit -- "
            "the hub registry has outgrown a single request",
        )
    return payload


def project(payload: dict[str, Any], registry: HubRegistry) -> dict[str, Any]:
    """Keep only the fields this system scores or displays, keyed by hub id.

    Nulls are preserved as nulls. A null in the NRI means "this hazard is not
    modelled for this county", which is NOT zero risk -- Denver has no coastal
    flooding score because coastal flooding does not apply there, and imputing
    0.0 would silently convert "not modelled" into "safe". The scoring engine
    renormalises its weights over the components that are present and
    discloses the omission; that decision needs a null here to act on.
    """
    by_fips: dict[str, dict[str, Any]] = {}
    for feature in payload["features"]:
        attributes = feature.get("attributes", {})
        fips = attributes.get(cfg.NRI_FIPS_FIELD)
        if fips:
            by_fips[str(fips)] = attributes

    snapshot: dict[str, Any] = {}
    missing: list[str] = []

    for hub in registry.hubs:
        attributes = by_fips.get(hub.nri_fips)
        if attributes is None:
            missing.append(f"{hub.id} ({hub.label}, NRI FIPS {hub.nri_fips})")
            continue

        hazards: dict[str, dict[str, Any]] = {}
        for hazard, code in cfg.NRI_HAZARD_CODES.items():
            hazards[hazard] = {
                "risk_score": attributes.get(f"{code}{cfg.NRI_RISK_SCORE_SUFFIX}"),
                "risk_rating": attributes.get(f"{code}{cfg.NRI_RISK_RATING_SUFFIX}"),
                "field_prefix": code,
            }

        snapshot[hub.id] = {
            "hub_id": hub.id,
            "nri_fips": hub.nri_fips,
            "hazards": hazards,
            "composite": {
                field: attributes.get(field) for field in cfg.NRI_COMPOSITE_FIELDS
            },
        }

    if missing:
        raise SystemExit(
            "FEMA NRI returned no row for these hubs -- check nri_fips in "
            "data/hubs.json:\n  " + "\n  ".join(missing)
        )
    return snapshot


def main() -> int:
    config = load_config()
    registry = load_hubs()
    logger.info("requesting NRI for %d hubs in one query", len(registry.hubs))

    try:
        payload = fetch_raw(registry, config)
    except UpstreamError as exc:
        logger.error("NRI fetch failed: %s", exc)
        return 1

    logger.info("service returned %d feature(s)", len(payload["features"]))

    config.raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = config.raw_dir / "nri_raw.json"
    raw_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("wrote raw response to %s", raw_path)

    snapshot = project(payload, registry)
    document = {
        "source": "FEMA National Risk Index, county level (v1.20.0, December 2025)",
        "service_url": cfg.NRI_QUERY_URL,
        "fips_field": cfg.NRI_FIPS_FIELD,
        "note": (
            "A null risk_score means the hazard is not modelled for that county, "
            "not that the county is safe from it."
        ),
        "hubs": snapshot,
    }
    config.nri_snapshot_path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    logger.info("wrote %d hub profiles to %s", len(snapshot), config.nri_snapshot_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
