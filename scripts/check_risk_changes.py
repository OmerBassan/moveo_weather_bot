"""Scheduled entry point for risk-change alerting.

    python -m scripts.check_risk_changes
    python -m scripts.check_risk_changes --webhook https://hooks.example.com/...
    python -m scripts.check_risk_changes --min-delta 5 --dry-run

Designed to be run by cron, Windows Task Scheduler, or a platform scheduler:

    0 */6 * * *  cd /srv && python -m scripts.check_risk_changes --webhook $HOOK

Exit codes are meaningful so a scheduler can act on them:
    0  ran successfully, no changes worth reporting
    1  ran successfully, changes found (and delivered, if a webhook was given)
    2  could not run -- live data unavailable, or the webhook rejected it
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

import httpx

from app.alerting import (
    DEFAULT_MIN_DELTA,
    diff,
    load_previous,
    render,
    save,
    state_path,
    take_snapshot,
    webhook_payload,
)
from app.config import load_config
from app.http_client import UpstreamError, build_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("risk_changes")
logging.getLogger("httpx").setLevel(logging.WARNING)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect hub risk-score changes.")
    parser.add_argument("--webhook", default=None, help="POST the changes to this URL")
    parser.add_argument(
        "--min-delta", type=float, default=DEFAULT_MIN_DELTA,
        help=f"points of movement worth reporting (default {DEFAULT_MIN_DELTA})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="detect and print, but do not update the stored baseline",
    )
    parser.add_argument("--json", default=None, help="write the payload here")
    args = parser.parse_args()

    config = load_config()

    try:
        current = take_snapshot(config)
    except UpstreamError as exc:
        # Live data is required. Scoring without it would silently produce a
        # baseline-only score and then report the difference as a risk change
        # the next time the NWS was reachable.
        logger.error("could not take a snapshot: %s", exc)
        return 2

    previous = load_previous(config)
    if previous is None:
        if not args.dry_run:
            save(current, config)
        logger.info(
            "no baseline existed; stored %d scores at %s. "
            "The next run will compare against this.",
            len(current.scores), current.taken_at,
        )
        return 0

    changes = diff(previous, current, args.min_delta)
    print(render(changes, previous.taken_at, current.taken_at))

    payload = webhook_payload(changes, previous, current)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
        logger.info("payload written to %s", args.json)

    if changes and args.webhook:
        try:
            with build_client(config) as client:
                response = client.post(args.webhook, json=payload)
                response.raise_for_status()
            logger.info("delivered %d change(s) to the webhook", len(changes))
        except httpx.HTTPError as exc:
            # The baseline is NOT advanced on a delivery failure, so the next
            # run reports the same changes rather than losing them silently.
            logger.error("webhook delivery failed, baseline not advanced: %s", exc)
            return 2

    if not args.dry_run:
        save(current, config)
        logger.info("baseline advanced to %s (%s)", current.taken_at, state_path(config).name)

    return 1 if changes else 0


if __name__ == "__main__":
    sys.exit(main())
