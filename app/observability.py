"""What the system did, recorded where someone can read it afterwards.

Two separate things live here, because they answer different questions.

LOGGING configures the root logger once, for the server. Without this the
application's own `logger.info` calls go nowhere: uvicorn configures its
loggers and leaves the root at WARNING with no handler, so every
`logger.info("matched 31 hub-alert pairs...")` in this codebase was written,
reviewed, and never once emitted. That is worse than having no logging, because
it reads like coverage that does not exist.

THE TURN RECORD is one structured line per answered question, appended to a
JSONL file: what was asked, what the agent decided, which tools it called, how
long it took, what it cost. Per-turn `usage` already goes back in the HTTP
response, but that reaches exactly one browser and is gone on refresh -- when
the question is "what did it do an hour ago", or "what has this demo cost",
there was nothing to read.

COST IS COMPUTED HERE, NOT IN THE UI. A rate table is a fact about the
provider, not about a rendering layer, and a number a reader will quote should
come from one place. The rates are recorded alongside each turn so a stored
record stays interpretable after prices change -- a log that says "$0.028"
without saying at what rate is a number nobody can check later.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.config import ROOT

logger = logging.getLogger(__name__)

# USD per million tokens, per model. Cache reads bill at a fraction of the
# input rate; cache writes at a premium over it. Kept as data so a price change
# is an edit here rather than a hunt through call sites.
#
# These are list rates for the Anthropic API and are recorded with every turn
# rather than assumed, because a stored cost is only auditable if the rate that
# produced it was stored too.
PRICING: dict[str, dict[str, float]] = {
    "claude-sonnet-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_read": 0.10, "cache_write": 1.25},
    "claude-opus-5": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
}
_DEFAULT_PRICING_KEY = "claude-sonnet-5"

TURN_LOG_PATH = Path(os.environ.get("WRA_TURN_LOG", str(ROOT / "data" / "turns.jsonl")))

# One writer lock. uvicorn serves requests on a thread pool, and two turns
# finishing together would otherwise interleave half-lines in the file.
_write_lock = threading.Lock()


def configure_logging(level: str | None = None) -> None:
    """Set up the root logger once, for the server process.

    Idempotent: uvicorn imports the app module in ways that can run this more
    than once, and stacking handlers duplicates every line.
    """
    root = logging.getLogger()
    if any(getattr(h, "_wra", False) for h in root.handlers):
        return

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    )
    handler._wra = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(getattr(logging, (level or os.environ.get("WRA_LOG_LEVEL", "INFO")).upper()))

    # One INFO line per HTTP request drowns everything this application has to
    # say -- a single turn makes 40 concurrent forecast calls. Both names are
    # silenced: the Anthropic SDK moved to httpx2, and the older name is still
    # used by our own client, so muting only one leaves half the noise.
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def price_for(model: str) -> tuple[str, dict[str, float]]:
    """The rate card for a model string like 'anthropic:claude-sonnet-5'.

    Falls back to the default rather than raising: an unpriced model must not
    take down a request, and the returned key records which card was used.
    """
    name = model.split(":", 1)[-1]
    for key, rates in PRICING.items():
        if name.startswith(key):
            return key, rates
    logger.warning("no pricing for model %r; costing at %s rates", model, _DEFAULT_PRICING_KEY)
    return _DEFAULT_PRICING_KEY, PRICING[_DEFAULT_PRICING_KEY]


def cost_usd(
    model: str, input_tokens: int, output_tokens: int, cached_tokens: int = 0
) -> dict[str, Any]:
    """What one turn cost, itemised.

    `input_tokens` is the uncached remainder, matching what the provider
    reports: cached tokens are billed separately and far cheaper, so adding
    them together would overstate every cached turn.
    """
    key, rates = price_for(model)
    fresh = input_tokens / 1e6 * rates["input"]
    cached = cached_tokens / 1e6 * rates["cache_read"]
    out = output_tokens / 1e6 * rates["output"]
    return {
        "total_usd": round(fresh + cached + out, 6),
        "input_usd": round(fresh, 6),
        "cached_input_usd": round(cached, 6),
        "output_usd": round(out, 6),
        "rate_card": key,
        "rates_usd_per_mtok": rates,
    }


@dataclass
class TurnRecord:
    """One answered question, start to finish."""

    request_id: str
    conversation_id: str
    question: str
    model: str
    started_at: str
    intent: str | None = None
    hazards: list[str] = field(default_factory=list)
    hub_count: int = 0
    tools_called: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    model_requests: int = 0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "request_id": self.request_id,
            "conversation_id": self.conversation_id,
            "started_at": self.started_at,
            "duration_seconds": round(self.duration_seconds, 2),
            "model": self.model,
            # Truncated: a turn record is an operational trace, not a
            # transcript store, and an unbounded user string in a log line is
            # how a log file becomes a data-retention question.
            "question": self.question[:200],
            "intent": self.intent,
            "hazards": self.hazards,
            "hub_count": self.hub_count,
            "tools_called": self.tools_called,
            "tokens": {
                "input": self.input_tokens,
                "output": self.output_tokens,
                "cached_input": self.cached_tokens,
                "model_requests": self.model_requests,
            },
            "cost": cost_usd(
                self.model, self.input_tokens, self.output_tokens, self.cached_tokens
            ),
        }
        if self.error:
            payload["error"] = self.error
        return payload

    def summary(self) -> str:
        """The one line a human reads while watching the server."""
        if self.error:
            return (
                f"[{self.request_id}] FAILED in {self.duration_seconds:.1f}s "
                f"-- {self.error}"
            )
        cost = self.as_dict()["cost"]["total_usd"]
        tools = ", ".join(self.tools_called) or "none"
        return (
            f"[{self.request_id}] {self.intent or '?'} "
            f"in {self.duration_seconds:.1f}s "
            f"| {self.hub_count} hub(s) | tools: {tools} "
            f"| {self.input_tokens}+{self.cached_tokens}c in / {self.output_tokens} out "
            f"| ${cost:.4f}"
        )


def write(record: TurnRecord) -> None:
    """Log the turn, and append it to the JSONL file.

    A failure to write the file must never fail the request: observability is
    there to explain the system, not to become a new way for it to break.
    """
    logger.info("%s", record.summary())
    try:
        TURN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.as_dict(), separators=(",", ":"))
        with _write_lock:
            with TURN_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError as exc:
        logger.warning("could not append to the turn log: %s", exc)


@contextmanager
def observe(question: str, conversation_id: str, model: str) -> Iterator[TurnRecord]:
    """Time a turn, record it, and let the exception through.

    The record is written on the way out whether the turn succeeded or raised,
    so a failing request is as visible as a working one -- the opposite is how
    an outage becomes invisible in its own logs.
    """
    record = TurnRecord(
        request_id=uuid.uuid4().hex[:8],
        conversation_id=conversation_id,
        question=question,
        model=model,
        started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    started = time.monotonic()
    try:
        yield record
    except Exception as exc:
        record.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        record.duration_seconds = time.monotonic() - started
        write(record)


def read_turns(limit: int = 50) -> list[dict[str, Any]]:
    """The most recent turns, newest first."""
    if not TURN_LOG_PATH.exists():
        return []
    lines = TURN_LOG_PATH.read_text(encoding="utf-8").splitlines()
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        if len(out) >= limit:
            break
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            # A truncated final line (killed mid-write) is not a reason to
            # refuse the whole history.
            continue
    return out


def totals() -> dict[str, Any]:
    """Everything the turn log has seen. What this demo has cost, in short."""
    turns = read_turns(limit=10**9)
    if not turns:
        return {"turns": 0, "total_cost_usd": 0.0}
    cost = sum(t.get("cost", {}).get("total_usd", 0.0) for t in turns)
    durations = sorted(t.get("duration_seconds", 0.0) for t in turns)
    errors = sum(1 for t in turns if t.get("error"))
    return {
        "turns": len(turns),
        "errors": errors,
        "total_cost_usd": round(cost, 4),
        "mean_cost_usd": round(cost / len(turns), 4),
        "median_duration_seconds": durations[len(durations) // 2],
        "slowest_duration_seconds": durations[-1],
        "total_tokens": {
            "input": sum(t.get("tokens", {}).get("input", 0) for t in turns),
            "cached_input": sum(t.get("tokens", {}).get("cached_input", 0) for t in turns),
            "output": sum(t.get("tokens", {}).get("output", 0) for t in turns),
        },
    }
