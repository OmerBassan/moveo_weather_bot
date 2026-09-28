"""Render the real UI headlessly against every response shape it must handle.

The UI is where the demo happens, and a Streamlit exception surfaces in the
browser rather than in any log a test would read -- so the only way to know it
renders is to run it. `AppTest` executes the actual script and re-raises
anything the app raised.

FIXTURES COME FROM THE ENGINE, NOT FROM THE AGENT. Each payload is built by
calling the deterministic scorer directly and shaping the result exactly as
`api.ChatResponse` does. That keeps the suite free of API calls and
deterministic, while still exercising real scores, real bands and real
component strings -- a hand-typed fixture would drift from the contract
silently, which is the failure this is meant to catch.

The shapes covered are the ones that differ structurally, not merely by
wording: a 40-row ranking where only the top 5 carry detail, a multi-hazard
comparison where one hub appears more than once, the two decline paths, a
transport error, and enough notes to trigger the footnote folding.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from app.hubs import load_hubs
from app.scoring.engine import Hazard, rank_hubs, rank_portfolio

APP = str(pathlib.Path(__file__).resolve().parents[1] / "ui" / "streamlit_app.py")

USAGE = {
    "input_tokens": 10214, "output_tokens": 731, "cached_input_tokens": 3859,
    "cache_hit_percent": 27.4, "model_requests": 2,
}
HEALTH = {
    "status": "ok", "hubs_loaded": 40, "active_conversations": 1,
    "attribution": {"text": "Weather data by Open-Meteo.com", "url": "https://open-meteo.com/"},
}
METHODOLOGY = {
    "note": "Prototype assumptions, not fitted coefficients.",
    "bands": [
        {"max": 20, "label": "Very Low"}, {"max": 40, "label": "Low"},
        {"max": 60, "label": "Moderate"}, {"max": 80, "label": "High"},
        {"max": 101, "label": "Very High"},
    ],
    "hazards": {
        "winter": {
            "label": "Winter weather disruption",
            "weights": {"baseline": 0.5, "historical": 0.3, "current": 0.2},
            "nri_dimensions": ["winter_weather", "cold_wave"], "combine": "max",
            "historical_metric": "snow_disruption_days", "methodology_note": None,
        },
        "hurricane": {
            "label": "Hurricane exposure",
            "weights": {"baseline": 0.6, "current": 0.4},
            "nri_dimensions": ["hurricane"], "combine": "max",
            "historical_metric": None,
            "methodology_note": "No historical component; see the design document.",
        },
    },
    "climatology_thresholds": {"snow_disruption_days_in": 1.0},
    "alert_severity_scores": {"Extreme": 100, "Severe": 75},
}


# The roster the sidebar groups by region, in the shape `/hubs` returns.
HUBS = {
    "count": len(load_hubs().hubs),
    "hubs": [
        {"hub_id": h.id, "name": h.label, "region": h.region,
         "county": h.county_name, "latitude": h.latitude, "longitude": h.longitude}
        for h in load_hubs().hubs
    ],
}


@pytest.fixture(scope="module")
def nri() -> dict[str, Any]:
    root = pathlib.Path(__file__).resolve().parents[1]
    return json.loads((root / "data" / "nri_snapshot.json").read_text(encoding="utf-8"))


def _assessments(hub_ids: tuple[str, ...], hazard: Hazard, nri: dict[str, Any]) -> list[dict]:
    """Exactly the shape `runner.assemble` produces, from real engine output."""
    registry = load_hubs()
    hubs = tuple(h for h in (registry.by_id(i) for i in hub_ids) if h is not None)
    ranked = rank_hubs(hubs, hazard, nri)
    leader = ranked[0].score
    rows = []
    for position, result in enumerate(ranked, start=1):
        driver = result.top_driver
        detailed = position <= 5  # mirrors the tool layer's DETAIL_ROWS budget
        hub = registry.by_id(result.hub_id)
        rows.append({
            "hub_id": result.hub_id, "hub": result.hub_label,
            "region": hub.region if hub else "", "hazard": result.hazard,
            "risk_score": result.score, "risk_band": result.band,
            "rank": position if len(ranked) > 1 else None,
            "gap_to_leader": round(leader - result.score, 1),
            "gap_to_next": (
                round(result.score - ranked[position].score, 1)
                if position < len(ranked) else None
            ),
            "main_drivers": [driver.name] if driver else [],
            "component_breakdown": list(result.breakdown()) if detailed else [],
            "evidence": list(result.evidence) if detailed else [],
            "active_alerts": [],
            # A risk row carries the structural score but NO tier: a ranking
            # must not be readable as an investment shortlist. Mirrors
            # `assemble` exactly, which is the only thing that makes this
            # fixture worth having.
            "structural_score": result.structural_score,
            "investability": None,
        })
    return rows


def _portfolio_assessments(nri: dict[str, Any]) -> list[dict]:
    """The shape `assemble` produces for intent='portfolio', from real engine
    output: tiers and structural scores, and no cross-hazard gaps."""
    registry = load_hubs()
    rows = []
    for position, entry in enumerate(rank_portfolio(registry.hubs, nri), start=1):
        lead = entry.lead
        detailed = position <= 3
        rows.append({
            "hub_id": entry.hub_id, "hub": entry.hub_label,
            "region": entry.region, "hazard": entry.lead_hazard,
            "risk_score": entry.score, "risk_band": entry.band,
            "rank": position,
            # Absent by design -- the rows span hazards.
            "gap_to_leader": None, "gap_to_next": None,
            "main_drivers": [lead.top_driver.name] if lead.top_driver else [],
            "component_breakdown": list(lead.breakdown()) if detailed else [],
            "evidence": list(lead.evidence) if detailed else [],
            "active_alerts": [],
            "structural_score": entry.structural_score,
            "investability": entry.investability,
        })

    # THE CONTRACT ALLOWS A NULL STRUCTURAL SCORE AND THIS PATH CANNOT PRODUCE
    # ONE: a hub's lead hazard is always one it is tierable on, because inland
    # flooding is modelled for every hub. But `structural_score` is Optional on
    # HubAssessment, a per-hazard view could carry None, and a bare f"{v:.1f}"
    # raises on it -- so the last row is forced to None to keep the formatter's
    # isna guard under test rather than merely present.
    if rows:
        rows[-1] = {**rows[-1], "structural_score": None, "investability": None}
    return rows


def _payloads(nri: dict[str, Any]) -> dict[str, dict[str, Any]]:
    registry = load_hubs()
    everything = tuple(h.id for h in registry.hubs)
    midwest = tuple(h.id for h in registry.hubs if h.region == "Midwest")
    pair = ("miami-fl", "houston-tx")

    base: dict[str, Any] = {
        "interpretation": [], "assumptions": [], "uncertainty": [],
        "sources": [], "usage": USAGE,
    }
    return {
        # 40 rows, only the first 5 carrying arithmetic.
        "rank_all_hubs": {
            **base, "intent": "rank",
            "answer": "Ranked by winter risk across all 40 hubs.",
            "interpretation_of_question": "Every hub, winter.",
            "assessments": _assessments(everything, "winter", nri),
            "interpretation": ["Lake-effect geography likely drives the top cluster."],
            "assumptions": ["20 qualifying snow days/year anchors a score of 100."],
            "uncertainty": ["Gridded reanalysis smooths localised snowfall bands."],
            "sources": ["FEMA National Risk Index v1.20.0", "NWS active alerts"],
        },
        # The investment view: Tier and Structural columns, no gap column, and
        # rows whose structural score is None.
        "portfolio": {
            **base, "intent": "portfolio",
            "answer": "Ranked for resilience investment across all three hazards.",
            "interpretation_of_question": "Which hubs to invest in.",
            "assessments": _portfolio_assessments(nri),
            "assumptions": [
                "Hubs are grouped by STRUCTURAL exposure, not by today's risk score."
            ],
            "sources": ["FEMA National Risk Index v1.20.0", "NWS active alerts"],
        },
        "rank_one_region": {
            **base, "intent": "rank",
            "answer": "Midwest hubs ranked by winter risk.",
            "interpretation_of_question": "Midwest only.",
            "assessments": _assessments(midwest, "winter", nri),
        },
        # The same hub appears twice, once per hazard.
        "compare_two_hazards": {
            **base, "intent": "compare",
            "answer": "Houston and Miami across hurricane and flood.",
            "interpretation_of_question": "Two hubs, two hazards.",
            "assessments": (
                _assessments(pair, "hurricane", nri) + _assessments(pair, "flood", nri)
            ),
            "interpretation": ["Both sit at FEMA's maximum hurricane index."],
        },
        "clarify": {
            **base, "intent": "clarify",
            "answer": "I need one detail first.",
            "interpretation_of_question": "Ambiguous hub reference.",
            "assessments": [],
            "clarification_question": "Portland, ME or Portland, OR?",
        },
        "out_of_scope": {
            **base, "intent": "out_of_scope",
            "answer": "That hazard is not covered.",
            "interpretation_of_question": "Wildfire request.",
            "assessments": [],
            "out_of_scope_reason": "This system models winter, hurricane and flood only.",
        },
        "transport_error": {
            **base, "intent": "error",
            "answer": "Could not reach the agent API.",
            "assessments": [],
            "uncertainty": ["ConnectError: connection refused"],
            "usage": {},
        },
        # Enough notes to trigger the footnote folding.
        "many_footnotes": {
            **base, "intent": "rank",
            "answer": "Scored, with a lot of caveats.",
            "interpretation_of_question": "Flood, two hubs.",
            "assessments": _assessments(pair, "flood", nri),
            "assumptions": [f"Assumption {i}." for i in range(5)],
            "uncertainty": [f"Uncertainty {i}." for i in range(4)],
            "sources": ["FEMA National Risk Index v1.20.0"],
        },
    }


class _Response:
    """The subset of httpx.Response the UI actually touches."""

    def __init__(self, data: Any) -> None:
        self._data = data

    def json(self) -> Any:
        return self._data

    def raise_for_status(self) -> None:
        return None


@pytest.mark.parametrize(
    "shape",
    ["rank_all_hubs", "rank_one_region", "portfolio", "compare_two_hazards",
     "clarify", "out_of_scope", "transport_error", "many_footnotes"],
)
def test_ui_renders_without_raising(shape: str, nri: dict[str, Any]) -> None:
    payload = _payloads(nri)[shape]

    def fake_get(url: str, **_: Any) -> _Response:
        if "/methodology" in url:
            return _Response(METHODOLOGY)
        if "/hubs" in url:
            return _Response(HUBS)
        return _Response(HEALTH)

    app = AppTest.from_file(APP, default_timeout=120)
    with (
        patch("httpx.get", fake_get),
        patch("httpx.post", lambda url, **kw: _Response(payload)),
        patch("httpx.delete", fake_get),
    ):
        app.run()
        assert not app.exception, [str(e.value) for e in app.exception]

        # Drive a real turn through the chat input, so the answer-rendering
        # path runs rather than only the empty initial page.
        assert app.chat_input, "the app exposes no chat input"
        app.chat_input[0].set_value("a question").run()
        assert not app.exception, [str(e.value) for e in app.exception]


def test_the_portfolio_table_shows_the_columns_the_ordering_uses(
    nri: dict[str, Any],
) -> None:
    """"Did it raise" would pass on a table that silently dropped Tier, and the
    whole point of the investment view is that the reader can see the column the
    order was built from. Also the crash guard: eight hubs carry a null
    structural score, and formatting one with a bare f"{v:.1f}" raises."""
    payload = _payloads(nri)["portfolio"]
    assert any(r["structural_score"] is None for r in payload["assessments"]), (
        "fixture no longer covers the null-structural-score case"
    )

    def fake_get(url: str, **_: Any) -> _Response:
        if "/methodology" in url:
            return _Response(METHODOLOGY)
        if "/hubs" in url:
            return _Response(HUBS)
        return _Response(HEALTH)

    app = AppTest.from_file(APP, default_timeout=120)
    with (
        patch("httpx.get", fake_get),
        patch("httpx.post", lambda url, **kw: _Response(payload)),
        patch("httpx.delete", fake_get),
    ):
        app.run()
        app.chat_input[0].set_value("which hubs should we invest in").run()
        assert not app.exception, [str(e.value) for e in app.exception]

        frames = [df.value for df in app.dataframe]
        assert frames, "the portfolio rendered no table"
        columns = list(frames[0].columns)
        assert "Tier" in columns and "Structural" in columns
        # The gap column is meaningless across hazards and must not appear.
        assert "Gap to #1" not in columns


def test_hub_roster_lists_every_hub_grouped_by_region() -> None:
    """The roster is the one place a reader can check what the portfolio is, so
    a silently-empty popover (a shape change in `/hubs`, say) must fail here."""
    def fake_get(url: str, **_: Any) -> _Response:
        if "/methodology" in url:
            return _Response(METHODOLOGY)
        if "/hubs" in url:
            return _Response(HUBS)
        return _Response(HEALTH)

    app = AppTest.from_file(APP, default_timeout=120)
    with patch("httpx.get", fake_get):
        app.run()
    assert not app.exception, [str(e.value) for e in app.exception]

    rendered = "".join(element.value for element in app.markdown)
    registry = load_hubs()
    for hub in registry.hubs:
        assert hub.label in rendered, f"{hub.label} is missing from the roster"
    for region in {h.region for h in registry.hubs}:
        assert region in rendered, f"region heading {region} is missing"
