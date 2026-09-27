"""The chat UI. A client of the API, with no import of the agent or the engine.

That is deliberate and is the point of the boundary: this file could be
deleted and replaced by curl, and nothing about the risk system would change.
It talks to `/chat` over HTTP like any other consumer would.

WHAT THIS UI IS FOR. Not to look finished -- to make the separation visible.
Every answer is drawn in three visually distinct trust tiers:

  1. ENGINE OUTPUT   -- deterministic scores, bands, ranks and the component
                        arithmetic, shown verbatim from the API. Monospace
                        numbers, band colours, a "deterministic" tag.
  2. EXPLANATION     -- `answer`: model-written prose about those scores.
  3. UNVERIFIED      -- `interpretation`: causal / speculative claims, boxed
                        with a dashed border and labelled as such.

Assumptions, uncertainty, sources and token usage sit below as footnotes.
Clarifications and out-of-scope declines get their own unmistakable panels.

This file renders only fields the API returns. It computes nothing about
risk: it sorts, filters and formats what the engine sent. Colours use fixed
mid-tone accents over translucent (rgba) fills so they read in both the light
and the dark Streamlit theme; nothing sets an opaque page background.

Requires Streamlit >= 1.46 (width="stretch", button icons, bordered containers).
"""

from __future__ import annotations

import html
import os
import uuid

import httpx
import pandas as pd
import streamlit as st

API_URL = os.environ.get("WRA_API_URL", "http://127.0.0.1:8000")
REQUEST_TIMEOUT = float(os.environ.get("WRA_UI_TIMEOUT", "180"))

# ------------------------------------------------------------------ palette --
# One fixed accent per band. Mid-tone so it holds contrast on white and on
# Streamlit's dark background; used for bars, pills and meters -- never as a
# page or card background (cards use the same colour at low alpha instead).
# `ink` is the text colour to put ON a solid pill of that band.

BANDS = ["Very High", "High", "Moderate", "Low", "Very Low"]
BAND_STYLE = {
    "Very High": {"hex": "#d63939", "rgb": "214,57,57", "ink": "#ffffff"},
    "High": {"hex": "#ec7a1c", "rgb": "236,122,28", "ink": "#1b1b1b"},
    "Moderate": {"hex": "#d9a400", "rgb": "217,164,0", "ink": "#1b1b1b"},
    "Low": {"hex": "#3b82c4", "rgb": "59,130,196", "ink": "#ffffff"},
    "Very Low": {"hex": "#3f9a5a", "rgb": "63,154,90", "ink": "#ffffff"},
}
NEUTRAL = {"hex": "#7c8591", "rgb": "124,133,145", "ink": "#ffffff"}

HAZARD_ICONS = {
    "winter": ":material/ac_unit:",
    "snow": ":material/ac_unit:",
    "hurricane": ":material/cyclone:",
    "flood": ":material/water:",
}

EXAMPLES = [
    ("Rank", ":material/leaderboard:",
     "Which hubs in the Midwest are most exposed to winter disruption?"),
    ("Compare", ":material/compare_arrows:",
     "Compare Miami and Houston in terms of hurricane and flood exposure."),
    ("Measure", ":material/straighten:",
     "What percentage of days in Denver last year had snowfall?"),
    ("Explain", ":material/help:",
     "Why is the Dallas hub's weather disruption risk high?"),
]

DETAIL_COUNT_NOTE = (
    "Only the top hubs carry the component arithmetic and evidence — "
    "the rest are returned with score, band and rank to stay within the token budget."
)

st.set_page_config(page_title="Weather Risk Intelligence", page_icon="⛈", layout="wide")

# Small, theme-neutral stylesheet: only borders, translucent fills and type.
# `color: inherit` everywhere text is placed, so the theme decides legibility.
st.markdown(
    """
<style>
.wr-tier { display:flex; align-items:center; gap:.5rem; margin:.9rem 0 .35rem 0;
  font-size:.72rem; letter-spacing:.08em; text-transform:uppercase; opacity:.8; }
.wr-tier .wr-tag { font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  border:1px solid rgba(128,128,128,.55); border-radius:4px; padding:1px 6px;
  letter-spacing:.04em; }
.wr-tier .wr-rule { flex:1; height:1px; background:rgba(128,128,128,.3); }
.wr-pill { display:inline-block; padding:1px 9px; border-radius:999px;
  font-size:.75rem; font-weight:600; line-height:1.5; white-space:nowrap; }
.wr-num { font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-variant-numeric:tabular-nums; }
.wr-rank { font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:1.9rem; font-weight:700; line-height:1; opacity:.9; }
.wr-rank small { font-size:.7rem; opacity:.6; display:block; font-weight:400;
  letter-spacing:.06em; text-transform:uppercase; margin-bottom:.2rem; }
.wr-hub { font-size:1.08rem; font-weight:650; line-height:1.25; }
.wr-sub { font-size:.8rem; opacity:.7; }
.wr-meter { height:6px; border-radius:3px; background:rgba(128,128,128,.22);
  overflow:hidden; margin-top:.35rem; }
.wr-meter > div { height:100%; border-radius:3px; }
.wr-score { font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-variant-numeric:tabular-nums; font-size:2.1rem; font-weight:700; line-height:1; }
.wr-score span { font-size:.85rem; opacity:.55; font-weight:400; }
.wr-gap { font-size:.75rem; opacity:.7; margin-top:.3rem; }
.wr-chip { display:inline-block; font-size:.75rem; padding:1px 8px; margin:2px 4px 2px 0;
  border-radius:4px; border:1px solid rgba(128,128,128,.35); }
.wr-unverified { border:1.5px dashed rgba(128,128,128,.6); border-radius:8px;
  padding:.7rem .9rem; margin:.4rem 0 .2rem 0; background:rgba(128,128,128,.06); }
.wr-unverified .wr-uhead { font-size:.72rem; letter-spacing:.08em; text-transform:uppercase;
  font-weight:700; opacity:.75; margin-bottom:.35rem; }
.wr-unverified ul { margin:0; padding-left:1.1rem; }
.wr-unverified li { font-style:italic; opacity:.9; margin:.15rem 0; }
.wr-panel { border-radius:10px; padding:1rem 1.1rem; margin:.25rem 0 .6rem 0; }
.wr-panel .wr-phead { display:flex; align-items:center; gap:.55rem;
  font-size:.74rem; letter-spacing:.09em; text-transform:uppercase; font-weight:700; }
.wr-panel .wr-glyph { width:1.6rem; height:1.6rem; border-radius:50%; display:inline-flex;
  align-items:center; justify-content:center; font-weight:800; font-size:.95rem; }
.wr-panel .wr-pbody { font-size:1.12rem; font-weight:550; margin:.55rem 0 .2rem 0; line-height:1.4; }
.wr-panel .wr-pfoot { font-size:.8rem; opacity:.72; }
.wr-foot { font-size:.78rem; opacity:.72; line-height:1.45; margin:.15rem 0; }
.wr-foot b { font-weight:650; }
.wr-usage { font-size:.7rem; opacity:.5; font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  margin-top:.4rem; }
.wr-understood { font-size:.82rem; opacity:.75; margin-bottom:.2rem; }
</style>
""",
    unsafe_allow_html=True,
)


# --------------------------------------------------------------------- api --

def api_get(path: str) -> dict | None:
    try:
        response = httpx.get(f"{API_URL}{path}", timeout=10)
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError):
        return None


@st.cache_data(ttl=300, show_spinner=False)
def api_methodology() -> dict | None:
    return api_get("/methodology")


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
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "answer": f"Could not reach the agent API at {API_URL}.",
            "intent": "error",
            "uncertainty": [f"{type(exc).__name__}: {exc}"],
            "assessments": [],
        }


# ----------------------------------------------------------------- helpers --

def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def band_style(band: str | None) -> dict:
    return BAND_STYLE.get(band or "", NEUTRAL)


def band_pill(band: str | None) -> str:
    s = band_style(band)
    return (
        f"<span class='wr-pill' style='background:{s['hex']};color:{s['ink']}'>"
        f"{esc(band or 'Unbanded')}</span>"
    )


def meter(score: float, band: str | None) -> str:
    pct = max(0.0, min(100.0, float(score)))
    return (
        f"<div class='wr-meter'><div style='width:{pct:.1f}%;"
        f"background:{band_style(band)['hex']}'></div></div>"
    )


def hazard_icon(hazard: str) -> str:
    low = (hazard or "").lower()
    for key, icon in HAZARD_ICONS.items():
        if key in low:
            return icon
    return ":material/thunderstorm:"


def fmt_score(value: float | None) -> str:
    return "—" if value is None else f"{float(value):.1f}"


def tier_label(text: str, tag: str | None = None) -> None:
    tag_html = f"<span class='wr-tag'>{esc(tag)}</span>" if tag else ""
    st.markdown(
        f"<div class='wr-tier'>{tag_html}<span>{esc(text)}</span>"
        f"<span class='wr-rule'></span></div>",
        unsafe_allow_html=True,
    )


def has_detail(row: dict) -> bool:
    return bool(row.get("component_breakdown") or row.get("evidence"))


def row_label(row: dict, multi_hazard: bool) -> str:
    return f"{row['hub']} · {row['hazard']}" if multi_hazard else row["hub"]


def ordered(assessments: list[dict]) -> list[dict]:
    """Present in the engine's rank order; unranked rows keep API order."""
    ranked = [a for a in assessments if a.get("rank") is not None]
    unranked = [a for a in assessments if a.get("rank") is None]
    return sorted(ranked, key=lambda a: a["rank"]) + unranked


# ------------------------------------------------------- tier 1: the engine --

def render_scorecard(row: dict, *, compact_head: bool = False) -> None:
    """One assessment as an authoritative scorecard. Detail is on demand."""
    s = band_style(row.get("risk_band"))
    with st.container(border=True):
        rank = row.get("rank")
        cols = st.columns([0.9, 4, 2.2], vertical_alignment="center") if rank else \
            st.columns([4, 2.2], vertical_alignment="center")
        if rank:
            with cols[0]:
                st.markdown(
                    f"<div class='wr-rank'><small>rank</small>#{int(rank)}</div>",
                    unsafe_allow_html=True,
                )
        with cols[-2]:
            st.markdown(
                f"<div class='wr-hub'>{esc(row['hub'])}</div>"
                f"<div class='wr-sub'>{esc(row.get('region', ''))} · "
                f"{esc(row.get('hazard', ''))} · <span class='wr-num'>"
                f"{esc(row.get('hub_id', ''))}</span></div>"
                f"<div style='margin-top:.4rem'>{band_pill(row.get('risk_band'))}</div>",
                unsafe_allow_html=True,
            )
        with cols[-1]:
            gap_bits = []
            gl = row.get("gap_to_leader")
            gn = row.get("gap_to_next")
            if rank == 1 or gl == 0:
                gap_bits.append("leader")
            elif gl is not None:
                gap_bits.append(f"−{float(gl):.1f} vs #1")
            if gn is not None:
                gap_bits.append(f"+{float(gn):.1f} over next")
            gap_html = (
                f"<div class='wr-gap wr-num'>{esc(' · '.join(gap_bits))}</div>"
                if gap_bits else ""
            )
            st.markdown(
                f"<div class='wr-score' style='color:{s['hex']}'>"
                f"{fmt_score(row.get('risk_score'))}<span> / 100</span></div>"
                f"{meter(row.get('risk_score') or 0, row.get('risk_band'))}{gap_html}",
                unsafe_allow_html=True,
            )

        if drivers := row.get("main_drivers"):
            st.markdown(
                "<div style='margin-top:.25rem'><span class='wr-sub'>Main drivers&nbsp;</span>"
                + "".join(f"<span class='wr-chip'>{esc(d)}</span>" for d in drivers)
                + "</div>",
                unsafe_allow_html=True,
            )

        breakdown = row.get("component_breakdown") or []
        evidence = row.get("evidence") or []
        alerts = row.get("active_alerts") or []
        if alerts:
            st.markdown(
                f":orange[:material/warning: **{len(alerts)} active NWS alert"
                f"{'s' if len(alerts) != 1 else ''}**]"
            )

        labels, bodies = [], []
        if breakdown:
            labels.append(f":material/calculate: Arithmetic ({len(breakdown)})")
            bodies.append("breakdown")
        if evidence:
            labels.append(f":material/fact_check: Evidence ({len(evidence)})")
            bodies.append("evidence")
        if alerts:
            labels.append(f":material/warning: Alerts ({len(alerts)})")
            bodies.append("alerts")
        if not labels:
            return

        with st.expander("Show how this score was built", icon=":material/functions:"):
            tabs = st.tabs(labels)
            for tab, body in zip(tabs, bodies):
                with tab:
                    if body == "breakdown":
                        st.caption("Engine output, verbatim — each component's points sum to the score.")
                        st.code("\n".join(breakdown), language=None, wrap_lines=True)
                    elif body == "evidence":
                        for line in evidence:
                            st.markdown(f"- {line}")
                    else:
                        for alert in alerts:
                            st.markdown(f"- :orange[:material/warning:] {alert}")


def render_rank_table(rows: list[dict], multi_hazard: bool, key: str) -> None:
    df = pd.DataFrame(
        {
            "Rank": [r.get("rank") for r in rows],
            "Hub": [row_label(r, multi_hazard) for r in rows],
            "Region": [r.get("region", "") for r in rows],
            "Hazard": [r.get("hazard", "") for r in rows],
            "Score": [float(r.get("risk_score") or 0) for r in rows],
            "Band": [r.get("risk_band", "") for r in rows],
            "Gap to #1": [r.get("gap_to_leader") for r in rows],
            "Detail": ["yes" if has_detail(r) else "—" for r in rows],
        }
    )

    def tint(band: str) -> str:
        s = BAND_STYLE.get(band)
        return f"background-color: rgba({s['rgb']},0.28); font-weight:600" if s else ""

    styled = df.style.map(tint, subset=["Band"]).format(
        {"Score": "{:.1f}", "Gap to #1": lambda v: "—" if pd.isna(v) else f"{v:.1f}"}
    )
    st.dataframe(
        styled,
        hide_index=True,
        width="stretch",
        height=min(38 + 35 * len(df), 460),
        key=key,
        column_config={
            "Rank": st.column_config.NumberColumn(width="small", format="#%d"),
            "Score": st.column_config.ProgressColumn(
                min_value=0, max_value=100, format="%.1f", width="medium"
            ),
            "Detail": st.column_config.TextColumn(
                width="small", help="Whether the engine returned arithmetic and evidence for this row"
            ),
        },
    )


def render_engine(payload: dict, turn_key: str) -> None:
    """Tier 1 -- everything the deterministic engine returned."""
    assessments = payload.get("assessments") or []
    if not assessments:
        return

    rows = ordered(assessments)
    multi_hazard = len({r.get("hazard") for r in rows}) > 1
    intent = payload.get("intent")

    n = len(rows)
    tier_label(
        f"Engine output · {n} assessment{'s' if n != 1 else ''} · "
        "computed, not generated",
        tag="DETERMINISTIC",
    )

    is_ranking = intent == "rank" or (n >= 5 and any(r.get("rank") for r in rows))

    if is_ranking:
        # Table only. A bar chart of 40 hubs spent a lot of vertical space to
        # re-state a column the table already sorts on, and the table carries
        # the region, hazard and gap columns the chart could not.
        render_rank_table(rows, multi_hazard, key=f"{turn_key}-table")

        detailed = [r for r in rows if has_detail(r)]
        if detailed:
            st.markdown(
                f"<div class='wr-sub' style='margin:.6rem 0 .2rem 0'>Top {len(detailed)} "
                "with component arithmetic</div>",
                unsafe_allow_html=True,
            )
            for row in detailed:
                render_scorecard(row)
        if n > len(detailed):
            st.caption(DETAIL_COUNT_NOTE)
        return

    # compare / explain / measure: cards, side by side when there are a few
    if n == 1:
        render_scorecard(rows[0])
        return
    per_row = 2 if n in (2, 4) else 3
    for start in range(0, n, per_row):
        cols = st.columns(per_row)
        for col, row in zip(cols, rows[start:start + per_row]):
            with col:
                render_scorecard(row)
    if any(not has_detail(r) for r in rows):
        st.caption(DETAIL_COUNT_NOTE)


# ----------------------------------------------- declines and clarifications --

def render_panel(kind: str, body: str, foot: str) -> None:
    spec = {
        "clarify": ("?", "One detail needed before scoring", {"hex": "#3b82c4", "rgb": "59,130,196"}),
        "out_of_scope": ("⊘", "Declined · outside this system's scope", {"hex": "#8a63d2", "rgb": "138,99,210"}),
        "error": ("!", "Request failed", BAND_STYLE["Very High"]),
    }[kind]
    glyph, title, c = spec
    st.markdown(
        f"<div class='wr-panel' style='border:1.5px solid rgba({c['rgb']},.75);"
        f"background:rgba({c['rgb']},.09)'>"
        f"<div class='wr-phead' style='color:{c['hex']}'>"
        f"<span class='wr-glyph' style='background:{c['hex']};color:#fff'>{glyph}</span>"
        f"{esc(title)}</div>"
        f"<div class='wr-pbody'>{esc(body)}</div>"
        f"<div class='wr-pfoot'>{esc(foot)}</div></div>",
        unsafe_allow_html=True,
    )


# ------------------------------------------------------------ tiers 2 and 3 --

def render_answer(answer: str, has_scores: bool) -> None:
    if not answer:
        return
    tier_label(
        "Explanation · model-written, grounded in the scores above"
        if has_scores else "Explanation · model-written",
        tag="LLM",
    )
    st.markdown(answer)


def render_unverified(lines: list[str]) -> None:
    items = "".join(f"<li>{esc(line)}</li>" for line in lines)
    st.markdown(
        "<div class='wr-unverified'><div class='wr-uhead'>"
        "Unverified · model reasoning beyond what the data establishes</div>"
        f"<ul>{items}</ul></div>",
        unsafe_allow_html=True,
    )


def render_footnotes(payload: dict) -> None:
    assumptions = payload.get("assumptions") or []
    uncertainty = payload.get("uncertainty") or []
    sources = payload.get("sources") or []
    if not (assumptions or uncertainty or sources):
        return

    st.markdown("<div style='height:.4rem'></div>", unsafe_allow_html=True)

    def block(title: str, lines: list[str]) -> str:
        if not lines:
            return ""
        return (
            f"<div class='wr-foot'><b>{esc(title)}</b> — "
            + " · ".join(esc(line) for line in lines)
            + "</div>"
        )

    notes = block("Assumptions", assumptions) + block("Uncertainty", uncertainty)
    total = len(assumptions) + len(uncertainty)
    if total > 6:
        with st.expander(f"Assumptions and uncertainty ({total})", icon=":material/info:"):
            st.markdown(notes, unsafe_allow_html=True)
    elif notes:
        st.markdown(notes, unsafe_allow_html=True)
    if sources:
        st.markdown(block("Sources", sources), unsafe_allow_html=True)


def render_usage(usage: dict | None) -> None:
    if not usage:
        return
    cached = usage.get("cached_input_tokens", 0)
    st.markdown(
        "<div class='wr-usage'>"
        f"{usage.get('input_tokens', 0):,} in ({cached:,} cached, "
        f"{usage.get('cache_hit_percent', 0)}%) · {usage.get('output_tokens', 0):,} out · "
        f"{usage.get('model_requests', 0)} model request(s)</div>",
        unsafe_allow_html=True,
    )


def render_turn(payload: dict, turn_key: str) -> None:
    intent = payload.get("intent")

    if understood := payload.get("interpretation_of_question"):
        st.markdown(
            f"<div class='wr-understood'>Understood as: <i>{esc(understood)}</i></div>",
            unsafe_allow_html=True,
        )

    if intent == "error":
        render_panel("error", payload.get("answer", ""),
                     "Nothing was scored. Check that the API is running, then try again.")
        render_footnotes(payload)
        return

    if question := payload.get("clarification_question"):
        render_panel("clarify", question,
                     "Nothing has been scored yet — reply below and the engine will run.")
    if reason := payload.get("out_of_scope_reason"):
        render_panel("out_of_scope", reason,
                     "No score was computed. This system ranks 40 US distribution hubs "
                     "by winter, hurricane and flood disruption risk.")

    render_engine(payload, turn_key)
    render_answer(payload.get("answer", ""), bool(payload.get("assessments")))

    if interpretation := payload.get("interpretation"):
        render_unverified(interpretation)

    render_footnotes(payload)
    render_usage(payload.get("usage"))


# ------------------------------------------------------------------- state --

if "conversation_id" not in st.session_state:
    st.session_state.conversation_id = f"ui-{uuid.uuid4().hex[:8]}"
if "turns" not in st.session_state:
    st.session_state.turns = []
if "pending" not in st.session_state:
    st.session_state.pending = None

# ----------------------------------------------------------------- sidebar --

with st.sidebar:
    st.markdown("### :material/thunderstorm: Weather Risk Intelligence")

    health = api_get("/health")
    if health:
        st.caption(f":green[●] API connected · **{health['hubs_loaded']}** hubs loaded")
    else:
        st.caption(f":red[●] API unreachable at `{API_URL}`")
        st.caption("Start it with `uvicorn app.api:app --port 8000`")

    st.markdown("**Try an example**")
    for i, (kind, icon, example) in enumerate(EXAMPLES):
        if st.button(
            example,
            key=f"ex{i}",
            icon=icon,
            help=f"{kind} question",
            type="tertiary",
            width="stretch",
        ):
            st.session_state.pending = example
            st.rerun()

    st.divider()

    st.markdown("**Risk bands**")
    st.markdown(
        "<div style='line-height:2'>" + " ".join(band_pill(b) for b in BANDS) + "</div>",
        unsafe_allow_html=True,
    )
    st.caption("Score 0–100 from the deterministic engine; the band is assigned by the engine too.")

    methodology = api_methodology()
    hazards = (methodology or {}).get("hazards") or {}
    st.markdown("**Hazards**")
    if hazards:
        st.caption("  \n".join(f"{hazard_icon(name)} {name}" for name in hazards))
    else:
        st.caption(":material/ac_unit: winter  \n:material/cyclone: hurricane  \n:material/water: flood")

    with st.expander("How scores are built", icon=":material/functions:"):
        if methodology:
            st.caption(methodology.get("note", ""))
            for name, config in hazards.items():
                st.markdown(f"{hazard_icon(name)} **{name}**")
                weights = config.get("weights") or {}
                if weights:
                    st.dataframe(
                        pd.DataFrame(
                            {"Component": list(weights), "Weight": [float(v) for v in weights.values()]}
                        ),
                        hide_index=True,
                        width="stretch",
                        key=f"weights-{name}",
                        column_config={
                            "Weight": st.column_config.ProgressColumn(
                                min_value=0, max_value=1, format="percent"
                            )
                        },
                    )
                if note := config.get("methodology_note"):
                    st.caption(note)
        else:
            st.caption("Methodology unavailable — the API is not reachable.")

    st.divider()
    if st.button("New conversation", icon=":material/add_comment:", width="stretch"):
        if health:
            try:
                httpx.delete(f"{API_URL}/chat/{st.session_state.conversation_id}", timeout=10)
            except httpx.HTTPError:
                pass
        st.session_state.conversation_id = f"ui-{uuid.uuid4().hex[:8]}"
        st.session_state.turns = []
        st.rerun()
    st.caption(f"Conversation `{st.session_state.conversation_id}`")

    st.caption(
        "Hazard baselines: FEMA National Risk Index v1.20.0. "
        "Live alerts: NOAA/NWS. Historical weather: "
        "[Weather data by Open-Meteo.com](https://open-meteo.com/) (CC BY 4.0)."
    )

# -------------------------------------------------------------------- main --

st.title("Weather Risk Intelligence")
st.markdown(
    "<div class='wr-sub' style='font-size:.9rem;margin-top:-.6rem'>"
    "Which distribution hubs are most exposed to weather disruption? "
    "<b>Scores come from a deterministic engine</b> — the language model reads your "
    "question and explains the result, and cannot produce or change a number.</div>",
    unsafe_allow_html=True,
)

if not st.session_state.turns and not st.session_state.pending:
    st.markdown("<div style='height:1.2rem'></div>", unsafe_allow_html=True)
    tier_label("How to read an answer")
    c1, c2, c3 = st.columns(3)
    with c1:
        with st.container(border=True):
            st.markdown("**:material/calculate: Engine output**")
            st.caption("Scores, bands, ranks and the arithmetic behind them. Deterministic.")
    with c2:
        with st.container(border=True):
            st.markdown("**:material/chat: Explanation**")
            st.caption("Model-written prose about those scores. It never introduces a number.")
    with c3:
        with st.container(border=True):
            st.markdown("**:material/help: Unverified**")
            st.caption("Causal or speculative reasoning, boxed with a dashed border.")

for i, turn in enumerate(st.session_state.turns):
    with st.chat_message("user"):
        st.markdown(turn["question"])
    with st.chat_message("assistant"):
        render_turn(turn["payload"], turn_key=f"t{i}")

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
        render_turn(payload, turn_key=f"t{len(st.session_state.turns)}")
    st.session_state.turns.append({"question": question, "payload": payload})
