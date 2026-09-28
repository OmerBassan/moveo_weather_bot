"""Tests that pin the token-cost shape of what the agent reads.

These are budget tests. They exist because tool payload size is invisible in
normal use -- nothing fails, the bill just grows -- so the only way it stays
fixed is to assert on it. A change that makes a payload larger should have to
justify itself by editing a number here.

Thresholds are deliberately loose (a ceiling, not a target) so that adding a
field is fine and adding a field to every one of forty rows is not.
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Iterator

import pytest

from app.agent import tools as tool_impl
from app.agent.runner import HISTORY_TURNS, model_settings, trim_history, usage_limits
from app.agent.tools import DETAIL_ROWS, Deps
from app.hubs import load_hubs
from app.scoring.engine import HAZARDS
from app.tools import nws


def approx_tokens(payload: object) -> int:
    """Rough token count: ~4 characters per token. Good enough for a budget
    ceiling, and it needs no tokeniser dependency."""
    return len(json.dumps(payload)) // 4


@pytest.fixture(scope="module")
def deps() -> Iterator[Deps]:
    """No live alerts and no live forecast: these tests must not depend on
    today's weather, or the budget would move with the storm season.

    THE FORECAST STUB IS NOT OPTIONAL. Omitting alerts is not enough --
    `rank_hubs_by_risk` and `explain_hub_risk` both call
    `Deps.ensure_forecasts`, which fetches an NWS gridpoint forecast for all
    forty hubs. That made this budget file depend on the weather it claims to
    exclude (live forecast text inflates the five detail rows by roughly 200
    tokens, more in a storm) and put forty network calls inside the unit suite,
    where one upstream 500 fails a test about token counts.

    Returning `{}` leaves `deps.forecasts` empty, so `score_hub` receives
    `forecast=None` -- the same inputs `scripts/regen_score_baseline` uses, so
    the budget and the golden baseline describe the same payloads.
    """
    patch = pytest.MonkeyPatch()
    patch.setattr(nws, "fetch_forecasts", lambda hubs, config=None: {})
    nri = json.loads(pathlib.Path("data/nri_snapshot.json").read_text(encoding="utf-8"))
    yield Deps(nri=nri, registry=load_hubs())
    patch.undo()


class TestPayloadBudgets:
    @pytest.mark.parametrize("hazard", HAZARDS)
    def test_full_ranking_stays_under_budget(self, deps: Deps, hazard: str) -> None:
        """The 40-hub ranking is the most-called and largest payload. It cost
        ~9,400 tokens when every row carried a full breakdown.

        EVERY HAZARD, NOT JUST WINTER. This assertion ran on `winter` alone --
        the cheapest of the three -- and so reported a comfortable margin while
        flood sat at the ceiling. Measured with the forecast stubbed:

            winter 2769    hurricane 2915    flood 3354

        Flood carries the most because its baseline combines two NRI hazards
        and nineteen hubs disclose an absent coastal component. Live forecast
        evidence adds roughly 200 tokens on top, which puts flood within a
        rounding error of this ceiling in production.

        So the headroom here is ~150 tokens on the worst hazard, not ~700. A
        field added to all forty rows costs ~400 and does not fit: anything
        needing per-row data belongs in a separate tool with its own budget,
        not bolted onto this payload.
        """
        payload = tool_impl.rank_hubs_by_risk(deps, hazard)
        assert approx_tokens(payload) < 3500

    def test_only_the_top_rows_carry_detail(self, deps: Deps) -> None:
        payload = tool_impl.rank_hubs_by_risk(deps, "winter")
        detailed = [row for row in payload["ranking"] if "components" in row]
        assert len(detailed) == DETAIL_ROWS
        # ...and the ones that do are the top ones.
        assert [row["rank"] for row in detailed] == list(range(1, DETAIL_ROWS + 1))

    def test_every_hub_is_still_ranked_and_scored(self, deps: Deps) -> None:
        """Trimming detail must not trim hubs: the answer to 'which hubs are
        most exposed' is the whole ordering."""
        payload = tool_impl.rank_hubs_by_risk(deps, "winter")
        assert len(payload["ranking"]) == 40
        for row in payload["ranking"]:
            assert "risk_score" in row and "risk_band" in row and "rank" in row

    def test_assumptions_are_deduplicated(self, deps: Deps) -> None:
        """40 hubs produced 41 assumption strings of which 2 were unique --
        1,802 tokens to say 86 tokens' worth."""
        payload = tool_impl.rank_hubs_by_risk(deps, "winter")
        assumptions = payload["assumptions"]
        assert len(assumptions) == len(set(assumptions))
        # And they are not also repeated on every row.
        assert not any("assumptions" in row for row in payload["ranking"])

    def test_single_hub_explain_keeps_its_own_assumptions(self, deps: Deps) -> None:
        """Deduplication is a property of a ranking, not of a single hub: one
        hub's caveats must travel with it."""
        payload = tool_impl.explain_hub_risk(deps, "denver-co", "flood")
        assert payload["assumptions"]

    def test_portfolio_stays_under_budget(self, deps: Deps) -> None:
        """Its own ceiling rather than the ranking's, because its rows are
        wider: each carries the driving hazards, the structural score and the
        exceedance. Measured at 2983 with the forecast stubbed, having started
        at 4019 before the per-hazard assumptions were moved to the response and
        the detail rows cut from five to three."""
        payload = tool_impl.portfolio_priorities(deps)
        assert approx_tokens(payload) < 3500

    def test_the_portfolio_still_covers_every_hub(self, deps: Deps) -> None:
        """Trimming detail must not trim hubs: a shortlist that silently drops
        candidates is worse than a long one."""
        payload = tool_impl.portfolio_priorities(deps)
        listed = sum(bucket["count"] for bucket in payload["tiers"].values())
        rows = sum(len(bucket["hubs"]) for bucket in payload["tiers"].values())
        assert listed == rows == 40 == payload["hub_count"]

    def test_the_portfolio_does_not_repeat_assumptions_per_row(
        self, deps: Deps
    ) -> None:
        payload = tool_impl.portfolio_priorities(deps)
        assert len(payload["assumptions"]) == len(set(payload["assumptions"]))
        for bucket in payload["tiers"].values():
            assert not any("assumptions" in row for row in bucket["hubs"])

    def test_hub_listing_is_compact(self, deps: Deps) -> None:
        payload = tool_impl.list_hubs(deps)
        assert approx_tokens(payload) < 450
        assert payload["count"] == 40


class TestDeterminism:
    def test_temperature_is_zero(self) -> None:
        """Set for models that honour it. NOTE: claude-sonnet-5 does not --
        it rejects sampling parameters and pydantic-ai warns that they are
        ignored. Determinism of the ANSWER therefore does not come from here;
        it comes from the scoring engine, which the model never touches. This
        test pins the setting, not a guarantee about the prose."""
        assert model_settings()["temperature"] == 0.0

    def test_caching_is_enabled_for_every_fixed_prefix(self) -> None:
        settings = model_settings()
        assert settings["anthropic_cache_instructions"]
        assert settings["anthropic_cache_tool_definitions"]
        assert settings["anthropic_cache_messages"]

    def test_reasoning_effort_is_low(self) -> None:
        """The reasoning lives in the scoring engine. Paying a thinking budget
        to re-derive what a tool already computed is pure waste here."""
        assert model_settings("anthropic:claude-sonnet-5")["anthropic_effort"] == "low"

    def test_effort_is_omitted_for_models_that_reject_it(self) -> None:
        """Haiku 4.5 answers 400 "This model does not support the effort
        parameter" -- so sending it fails every request rather than degrading.
        Observed, not inferred from the version number."""
        assert "anthropic_effort" not in model_settings(
            "anthropic:claude-haiku-4-5-20251001"
        )

    def test_output_is_bounded(self) -> None:
        assert 0 < model_settings()["max_tokens"] <= 4000


class TestRunawayBounds:
    def test_tool_calls_are_capped(self) -> None:
        limits = usage_limits()
        assert limits.tool_calls_limit is not None
        assert limits.tool_calls_limit <= 10
        assert limits.request_limit is not None


class TestHistoryTrimming:
    def _history(self, turns: int) -> list[object]:
        from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

        history: list[object] = []
        for i in range(turns):
            history.append(ModelRequest(parts=[UserPromptPart(content=f"q{i}")]))
            history.append(ModelResponse(parts=[TextPart(content=f"a{i}")]))
        return history

    def test_short_history_is_untouched(self) -> None:
        history = self._history(2)
        assert trim_history(history) == history

    def test_long_history_is_capped_to_the_recent_turns(self) -> None:
        from pydantic_ai.messages import ModelRequest

        trimmed = trim_history(self._history(10))
        assert trimmed is not None
        requests = [m for m in trimmed if isinstance(m, ModelRequest)]
        assert len(requests) == HISTORY_TURNS

    def test_trimming_starts_on_a_request_boundary(self) -> None:
        """A window that began mid-turn would hand the model a response whose
        question it cannot see, or a tool call with no result."""
        from pydantic_ai.messages import ModelRequest

        trimmed = trim_history(self._history(10))
        assert trimmed is not None
        assert isinstance(trimmed[0], ModelRequest)

    def test_empty_history_is_none_not_an_empty_list(self) -> None:
        assert trim_history(None) is None
        assert trim_history([]) is None
