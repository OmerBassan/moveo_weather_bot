"""One HTTP client, with the retry policy the three upstreams actually need.

Each upstream documents a different failure mode and this module is where
those differences live, so no call site has to remember them:

  NWS         rate limit is deliberately undocumented ("allows a generous
              amount for typical use"); on exceeding it a request errors and
              "may be retried after the limit clears (typically within 5
              seconds)". Retries are therefore worth taking, and the required
              identifying User-Agent is attached here rather than per call.
  ArcGIS      anonymous and uncapped for our volume, but it answers HTTP 200
              with an `error` object in the JSON body rather than an HTTP
              error status -- so status-only checking silently accepts
              failures. `get_json` raises on that shape.
  Open-Meteo  10,000 calls/day, which we cannot approach; transient network
              failure is the only realistic error.

Retries cover connection errors, timeouts and 429/5xx only. A 4xx other than
429 is a contract error -- a bad field name, a malformed where clause -- and
retrying it just delays the traceback that should have been read the first
time.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.config import AppConfig

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class UpstreamError(RuntimeError):
    """A typed failure crossing the network boundary.

    Raised rather than returned because every caller of this module is a
    build script or a tool wrapper that cannot proceed without the data; the
    tool layer above converts it into a structured error for the agent.
    """

    def __init__(self, url: str, detail: str) -> None:
        super().__init__(f"{url}: {detail}")
        self.url = url
        self.detail = detail


def build_client(config: AppConfig) -> httpx.Client:
    """The User-Agent is set for every request, not just NWS ones: it
    identifies this application to all three upstreams and only NWS is
    documented to care."""
    return httpx.Client(
        timeout=config.http_timeout_seconds,
        headers={"User-Agent": config.nws_user_agent, "Accept": "application/json"},
        follow_redirects=True,
    )


def get_json(
    client: httpx.Client,
    url: str,
    params: dict[str, Any],
    *,
    max_retries: int,
) -> dict[str, Any]:
    """GET `url` and return a parsed JSON object, or raise `UpstreamError`."""
    payload = _request(client, url, params, max_retries=max_retries)
    if not isinstance(payload, dict):
        raise UpstreamError(url, f"expected a JSON object, got {type(payload).__name__}")
    return payload


def _request(
    client: httpx.Client,
    url: str,
    params: dict[str, Any],
    *,
    max_retries: int,
) -> Any:
    """The retry loop.

    `params` is passed to httpx rather than interpolated into the URL, so
    quoting of things like `STCOFIPS IN ('08031', ...)` is the library's
    problem and not a hand-rolled escaping bug.
    """
    last_detail = "no attempt made"

    for attempt in range(1, max_retries + 1):
        try:
            response = client.get(url, params=params)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last_detail = f"{type(exc).__name__}: {exc}"
            logger.warning("attempt %d/%d failed for %s: %s", attempt, max_retries, url, last_detail)
        else:
            if response.status_code in _RETRYABLE_STATUS:
                last_detail = f"HTTP {response.status_code}"
                logger.warning(
                    "attempt %d/%d got %s from %s", attempt, max_retries, last_detail, url
                )
            elif response.is_error:
                # Not retryable: a contract error. Include the body, truncated,
                # because ArcGIS and NWS both explain the actual problem there.
                raise UpstreamError(
                    url, f"HTTP {response.status_code}: {response.text[:500]}"
                )
            else:
                return _parse_body(url, response)

        if attempt < max_retries:
            # NWS documents the limit clearing "typically within 5 seconds";
            # 2s, 4s, 8s spans that without hammering it.
            time.sleep(2**attempt)

    raise UpstreamError(url, f"exhausted {max_retries} attempts; last failure: {last_detail}")


def get_json_list(
    client: httpx.Client,
    url: str,
    params: dict[str, Any],
    *,
    max_retries: int,
) -> list[dict[str, Any]]:
    """As `get_json`, for an endpoint that answers with a top-level array.

    Open-Meteo switches shape based on the request: a single coordinate pair
    returns an object, several return an array of them. This wraps the object
    form into a one-element list so callers have one shape to handle, rather
    than each caller re-deriving that rule.
    """
    payload = _request(client, url, params, max_retries=max_retries)
    if isinstance(payload, dict):
        return [payload]
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                raise UpstreamError(url, f"array held a {type(item).__name__}, not an object")
        return payload
    raise UpstreamError(url, f"expected an object or array, got {type(payload).__name__}")


def _parse_body(url: str, response: httpx.Response) -> Any:
    try:
        payload = response.json()
    except ValueError as exc:
        raise UpstreamError(url, f"response was not JSON: {exc}") from exc

    # An array body carries no error envelope to inspect; per-item checking is
    # the caller's, since only it knows what an item should contain.
    if not isinstance(payload, dict):
        return payload

    # ArcGIS answers 200 with {"error": {...}} for a bad field or where clause.
    # Open-Meteo answers {"error": true, "reason": "..."}. Both would otherwise
    # be read as success and fail much later, somewhere less informative.
    if "error" in payload:
        error = payload["error"]
        detail = payload.get("reason") or (
            error.get("message") if isinstance(error, dict) else str(error)
        )
        raise UpstreamError(url, f"upstream reported an error: {detail}")

    return payload
