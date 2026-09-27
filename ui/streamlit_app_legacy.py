"""The chat UI. A client of the API, with no import of the agent or the engine.

That is deliberate and is the point of the boundary: this file could be
deleted and replaced by curl, and nothing about the risk system would change.
It talks to `/chat` over HTTP like any other consumer would.

WHAT THIS UI IS FOR. Not to look finished -- to make the separation visible.
Every answer shows the deterministic scores beside the prose, the component
arithmetic that produced them, and the assumptions the engine attached. A
reviewer should be able to check any number on screen against the breakdown
directly under it, which is exactly what a system whose central claim is
"the LLM does not compute the risk" has to let you do.
"""

from __future__ import annotations

import os
import uuid

import httpx
import streamlit as st

API_URL = os.environ.get("WRA_API_URL", "http://127.0.0.1:8000")
REQUEST_TIMEOUT = float(os.environ.get("WRA_UI_TIMEOUT", "180"))

BAND_COLOURS = {
    "Very High": "#b91c1c",
    "High": "#ea580c",
    "Moderate": "#ca8a04",
    "Low": "#0891b2",
    "Very Low": "#15803d",
}

EXAMPLES = [
    "Which hubs in the Midwest are most exposed to winter disruption?",
    "Compare Miami and Houston in terms of hurricane and flood exposure.",
    "What percentage of days in Denver last year had snowfall?",
    "Why is the Dallas hub's weather disruption risk high?",
]

st.set_page_config(page_title="Weather Risk Intelligence", page_icon="⛈", layout="wide")


def api_get(path: str) -> dict | None:
    try:
        response = httpx.get(f"{API_URL}{path}", timeout=10)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPError:
        return None


def api_chat(message: str, conversation_id: str) -> dict:
    """A transport failure is rendered in the same shape as an answer, so the
    chat history never contains a half-rendered turn."""
    try:
        response = httpx.post(
            f"{API_URL}/chat",
            json={"message": message, "conversation_id": conversation_id},
            timeout=REQUEST_TIMEOUT,
        )
        return response.json()
    except httpx.HTTPError as exc:
        return {
            "answer": f"Could not reach the agent API at {API_URL}.",
            "intent": "error",
            "uncertainty": [f"{type(exc).__name__}: {exc}"],
            "assessments": [],
        }


def render_assessments(assessments: list[dict]) -> None:
    """The deterministic half of the answer, shown next to the prose."""
    if not assessments:
        return

    st.caption("Deterministic scores — computed by the engine, not the model")
    for row in assessments[:10]:
        colour = BAND_COLOURS.get(row["risk_band"], "#64748b")
        rank = f"#{row['rank']} " if row.get("rank") else ""
        header = (
            f"{rank}**{row['hub']}** · "
            f":{'red' if row['risk_band'] in ('High', 'Very High') else 'orange'}"
            f"[{row['risk_score']} / 100] · {row['risk_band']}"
        )
        with st.expander(header, expanded=False):
            st.markdown(
                f"<div style='border-left:4px solid {colour};padding-left:10px'>"
                f"<b>{row['hazard']}</b> · rank {row.get('rank', '—')}"
                f"</div>",
                unsafe_allow_html=True,
            )
            if row.get("component_breakdown"):
                st.markdown("**How this score was built**")
                for line in row["component_breakdown"]:
                    st.markdown(f"- `{line}`")
            if row.get("evidence"):
                st.markdown("**Evidence**")
                for line in row["evidence"]:
                    st.markdown(f"- {line}")
            if row.get("active_alerts"):
                st.markdown("**Active NWS alerts**")
                for alert in row["active_alerts"]:
                    st.markdown(f"- ⚠ {alert}")

    if len(assessments) > 10:
        st.caption(f"…and {len(assessments) - 10} more hubs scored.")


def render_turn(payload: dict) -> None:
    st.markdown(payload.get("answer", ""))

    if question := payload.get("clarification_question"):
        st.info(f"**I need one detail:** {question}")
    if reason := payload.get("out_of_scope_reason"):
        st.warning(f"**Outside this system's scope:** {reason}")

    render_assessments(payload.get("assessments", []))

    if interpretation := payload.get("interpretation"):
        st.markdown("**Interpretation** — reasoning beyond what the data establishes")
        for line in interpretation:
            st.markdown(f"- _{line}_")

    assumptions = payload.get("assumptions", [])
    uncertainty = payload.get("uncertainty", [])
    if assumptions or uncertainty:
        with st.expander(
            f"Assumptions and uncertainty ({len(assumptions) + len(uncertainty)})"
        ):
            for line in assumptions:
                st.markdown(f"- **Assumption:** {line}")
            for line in uncertainty:
                st.markdown(f"- **Uncertainty:** {line}")

    if sources := payload.get("sources"):
        st.caption("Sources: " + " · ".join(sources))
    if usage := payload.get("usage"):
        st.caption(
            f"{usage.get('input_tokens', 0):,} in / {usage.get('output_tokens', 0):,} out · "
            f"{usage.get('cache_hit_percent', 0)}% of input served from cache · "
            f"{usage.get('model_requests', 0)} model request(s)"
        )


# ------------------------------------------------------------------- state --

if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = f"ui-{uuid.uuid4().hex[:8]}"
if "turns" not in st.session_state:
    st.session_state.turns = []
if "pending" not in st.session_state:
    st.session_state.pending = None

# ----------------------------------------------------------------- sidebar --

with st.sidebar:
    st.header("Weather Risk Intelligence")
    st.caption("40 US distribution hubs · winter, hurricane, flood")

    health = api_get("/health")
    if health:
        st.success(f"API connected · {health['hubs_loaded']} hubs")
    else:
        st.error(f"API unreachable at {API_URL}")
        st.caption("Start it with: `uvicorn app.api:app --port 8000`")

    st.divider()
    st.subheader("Try an example")
    for i, example in enumerate(EXAMPLES):
        if st.button(example, key=f"ex{i}", use_container_width=True):
            st.session_state.pending = example
            st.rerun()

    st.divider()
    if st.button("New conversation", use_container_width=True):
        httpx.delete(
            f"{API_URL}/chat/{st.session_state.conversation_id}", timeout=10
        ) if health else None
        st.session_state.conversation_id = f"ui-{uuid.uuid4().hex[:8]}"
        st.session_state.turns = []
        st.rerun()

    with st.expander("How scores are built"):
        methodology = api_get("/methodology")
        if methodology:
            st.caption(methodology["note"])
            for name, config in methodology["hazards"].items():
                weights = " + ".join(
                    f"{int(v * 100)}% {k}" for k, v in config["weights"].items()
                )
                st.markdown(f"**{name}** — {weights}")
                if note := config.get("methodology_note"):
                    st.caption(note)

    st.divider()
    st.caption(
        "Hazard baselines: FEMA National Risk Index v1.20.0. "
        "Live alerts: NOAA/NWS. Historical weather: "
        "[Weather data by Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0)."
    )

# -------------------------------------------------------------------- main --

st.title("Weather Risk Intelligence Agent")
st.caption(
    "Ask which hubs are most exposed to weather disruption. Risk scores are "
    "computed by a deterministic engine — the language model interprets your "
    "question and explains the result, and cannot produce or change a score."
)

for turn in st.session_state.turns:
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant"):
        render_turn(turn["payload"])

question = st.chat_input("e.g. Which hubs in the Midwest are most exposed to winter disruption?")
if st.session_state.pending:
    question = st.session_state.pending
    st.session_state.pending = None

if question:
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with st.spinner("Retrieving hazard data and scoring hubs…"):
            payload = api_chat(question, st.session_state.conversation_id)
        render_turn(payload)
    st.session_state.turns.append({"question": question, "payload": payload})
