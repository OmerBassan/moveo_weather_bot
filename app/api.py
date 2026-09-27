"""The FastAPI boundary. The UI is a client of this, never of the agent.

That separation is the assignment's "expose the agent through an API that the
chat interface uses", and it is also what makes the system demonstrable: the
same `/chat` that Streamlit calls can be driven by curl, by the eval harness,
or by another service, and none of them import the agent.

CONVERSATION STATE IS IN MEMORY, ON PURPOSE. A dict keyed by conversation_id,
bounded, lost on restart. This is a prototype for a take-home, and a database
here would be infrastructure that demonstrates nothing about the risk system.
The limitation is stated rather than hidden -- see the design document -- and
the boundary is narrow enough that swapping in Redis is one class.

The error shape is part of the contract: a failure comes back as a typed JSON
body with the same `answer`/`uncertainty` fields a success has, so the UI
never has to branch on status codes to render something useful.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from pydantic_ai.exceptions import UsageLimitExceeded

from app.agent.runner import build_agent, run_turn
from app.alerting import (
    DEFAULT_MIN_DELTA,
    diff as risk_diff,
    load_previous,
    save,
    take_snapshot,
    webhook_payload,
)
from app.http_client import UpstreamError
from app.config import (
    OPEN_METEO_ATTRIBUTION_TEXT,
    OPEN_METEO_ATTRIBUTION_URL,
    load_config,
)
from app.hubs import load_hubs
from app.scoring.engine import HAZARDS, load_weights

logger = logging.getLogger(__name__)

# How many conversations are retained, and how many turns of each. Both bounded
# because an unbounded server-side dict keyed by a client-supplied string is a
# memory leak with a nice name.
MAX_CONVERSATIONS = 200
MAX_TURNS_PER_CONVERSATION = 40

_conversations: OrderedDict[str, list[Any]] = OrderedDict()
_state: dict[str, Any] = {}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str = Field(default="default", min_length=1, max_length=128)


class ChatResponse(BaseModel):
    """Mirrors `contract.AgentResponse`, plus transport-level metadata.

    Declared as a Pydantic model rather than returned as a bare dict so the
    response shape is in the OpenAPI schema -- the contract is published, not
    implied by whatever the handler happened to build.
    """

    answer: str
    intent: str
    interpretation_of_question: str = ""
    assessments: list[dict[str, Any]] = Field(default_factory=list)
    interpretation: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    uncertainty: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    clarification_question: str | None = None
    out_of_scope_reason: str | None = None
    conversation_id: str = "default"
    usage: dict[str, Any] = Field(default_factory=dict)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the snapshots and build the agent ONCE, at startup.

    Per-request construction would re-read a 2.9 MB climatology file and
    rebuild five tool schemas on every question, and would make the first
    turn's latency a property of disk rather than of the model.
    """
    config = load_config()
    _state["nri"] = json.loads(config.nri_snapshot_path.read_text(encoding="utf-8"))
    _state["agent"] = build_agent()
    _state["registry"] = load_hubs()
    logger.info("loaded %d hubs and built the agent", len(_state["registry"].hubs))
    yield
    _state.clear()


app = FastAPI(
    title="Weather Risk Intelligence Agent",
    version="1.0.0",
    description=(
        "Ranks 40 US distribution hubs by weather disruption exposure. Scores are "
        "computed by a deterministic engine; the LLM interprets the question and "
        "explains the result, and cannot produce or alter a score."
    ),
    lifespan=lifespan,
)


def _remember(conversation_id: str, messages: list[Any]) -> None:
    _conversations[conversation_id] = messages[-MAX_TURNS_PER_CONVERSATION:]
    _conversations.move_to_end(conversation_id)
    while len(_conversations) > MAX_CONVERSATIONS:
        evicted, _ = _conversations.popitem(last=False)
        logger.info("evicted conversation %s", evicted)


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> Any:
    """One conversational turn."""
    history = _conversations.get(request.conversation_id)
    try:
        turn = run_turn(
            request.message,
            _state["nri"],
            history=history,
            agent=_state["agent"],
        )
    except UsageLimitExceeded as exc:
        # The agent hit its tool-call ceiling: a loop, not a user error.
        logger.warning("usage limit hit on %s: %s", request.conversation_id, exc)
        return JSONResponse(
            status_code=503,
            content=ChatResponse(
                answer=(
                    "I could not answer that within this system's tool-call budget. "
                    "Try asking about a specific hub or hazard."
                ),
                intent="error",
                uncertainty=[f"The request exceeded its internal limits: {exc}"],
                conversation_id=request.conversation_id,
            ).model_dump(),
        )
    except Exception as exc:  # noqa: BLE001 - boundary handler, see below
        # The outermost boundary of a web service is the one place a broad
        # catch is correct: an unhandled exception here would return an HTML
        # traceback to a chat UI. It is logged with its type and re-shaped
        # into the response contract, never silently swallowed.
        logger.exception("chat failed for conversation %s", request.conversation_id)
        return JSONResponse(
            status_code=500,
            content=ChatResponse(
                answer="Something went wrong answering that question.",
                intent="error",
                uncertainty=[f"{type(exc).__name__}: {exc}"],
                conversation_id=request.conversation_id,
            ).model_dump(),
        )

    _remember(request.conversation_id, turn.messages)

    payload = turn.response.as_dict()
    payload["conversation_id"] = request.conversation_id
    payload["usage"] = {
        "input_tokens": turn.input_tokens,
        "output_tokens": turn.output_tokens,
        "cached_input_tokens": turn.cached_tokens,
        "cache_hit_percent": round(turn.cache_hit_rate, 1),
        "model_requests": turn.requests,
    }
    return payload


@app.delete("/chat/{conversation_id}")
def reset_conversation(conversation_id: str) -> dict[str, Any]:
    """Drop a conversation's history. The UI's 'new chat' button."""
    existed = _conversations.pop(conversation_id, None) is not None
    return {"conversation_id": conversation_id, "cleared": existed}


@app.get("/hubs")
def hubs() -> dict[str, Any]:
    """The hub registry, for the UI's picker and for orientation."""
    registry = _state.get("registry") or load_hubs()
    return {
        "count": len(registry.hubs),
        "hazards": list(HAZARDS),
        "hubs": [
            {
                "hub_id": h.id,
                "name": h.label,
                "region": h.region,
                "county": h.county_name,
                "latitude": h.latitude,
                "longitude": h.longitude,
            }
            for h in registry.hubs
        ],
    }


@app.get("/methodology")
def methodology() -> dict[str, Any]:
    """The scoring weights and thresholds, served from the same YAML the engine
    reads. Published so a reviewer can check that what is documented is what
    actually runs, rather than taking the design document's word for it."""
    weights = load_weights()
    return {
        "note": (
            "These weights are prototype assumptions, not fitted coefficients. "
            "No hub-downtime dataset was available to calibrate them."
        ),
        "bands": weights["bands"],
        "hazards": {
            name: {
                "label": config["label"],
                "weights": config["weights"],
                "nri_dimensions": config["baseline_hazards"],
                "combine": config["baseline_combine"],
                "historical_metric": config.get("historical_metric"),
                "methodology_note": config.get("methodology_note"),
            }
            for name, config in weights["hazards"].items()
        },
        "climatology_thresholds": weights["climatology_thresholds"],
        "alert_severity_scores": weights["alert_severity_scores"],
    }


@app.post("/alerts/check")
def check_risk_changes(min_delta: float = DEFAULT_MIN_DELTA, commit: bool = False) -> Any:
    """Re-score every hub and report what moved since the stored baseline.

    The inbound half of the bonus: a scheduler, a webhook or a demo button can
    trigger a check on demand. The outbound half -- POSTing changes to a
    webhook URL -- lives in scripts/check_risk_changes.py, which is what a cron
    entry runs.

    `commit=false` by default so a demo can be run repeatedly against the same
    baseline without consuming it. A scheduled run passes commit=true, or uses
    the script.
    """
    try:
        current = take_snapshot(load_config())
    except UpstreamError as exc:
        return JSONResponse(
            status_code=503,
            content={
                "error": "live weather data unavailable",
                "detail": exc.detail,
                "note": (
                    "Scoring without live data would produce baseline-only scores "
                    "and report the difference as a risk change on the next run."
                ),
            },
        )

    previous = load_previous()
    if previous is None:
        save(current)
        return {
            "baseline_created": True,
            "scores_stored": len(current.scores),
            "taken_at": current.taken_at,
            "note": "No baseline existed. The next check compares against this one.",
        }

    changes = risk_diff(previous, current, min_delta)
    payload = webhook_payload(changes, previous, current)
    if commit:
        save(current)
        payload["baseline_advanced"] = True
    return payload


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok" if _state.get("agent") else "starting",
        "hubs_loaded": len(_state["registry"].hubs) if "registry" in _state else 0,
        "active_conversations": len(_conversations),
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "attribution": {
            "text": OPEN_METEO_ATTRIBUTION_TEXT,
            "url": OPEN_METEO_ATTRIBUTION_URL,
        },
    }
