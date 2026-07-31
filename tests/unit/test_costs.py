"""Why this exists: WP-V2-5 budget guard + cost dashboard behavior — the guard
must be a no-op at default config, fail open on DB errors, cap spammy customers
politely, downgrade (never block) when the daily budget is hit, and the report
must aggregate correctly.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.config import settings
from services.costs import BudgetStatus, check_budget, cost_report


def _llm_response(content: str = "Dạ có ạ") -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class TestCheckBudget:
    @pytest.mark.asyncio
    async def test_default_config_runs_no_queries(self):
        """Both limits default to 0 → the guard must not touch the DB at all."""
        db = AsyncMock()
        status = await check_budget("cust_1", db)
        assert status.over_daily_budget is False
        assert status.over_customer_cap is False
        db.scalar.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_db_returns_all_clear(self):
        status = await check_budget("cust_1", None)
        assert status == BudgetStatus()

    @pytest.mark.asyncio
    async def test_daily_limit_reached(self, monkeypatch):
        monkeypatch.setattr(settings, "DAILY_COST_LIMIT_USD", 5.0)
        db = AsyncMock()
        db.scalar.return_value = 5.01
        status = await check_budget(None, db)
        assert status.over_daily_budget is True
        assert status.daily_cost_usd == pytest.approx(5.01)

    @pytest.mark.asyncio
    async def test_daily_limit_not_reached(self, monkeypatch):
        monkeypatch.setattr(settings, "DAILY_COST_LIMIT_USD", 5.0)
        db = AsyncMock()
        db.scalar.return_value = 1.2
        status = await check_budget(None, db)
        assert status.over_daily_budget is False

    @pytest.mark.asyncio
    async def test_customer_cap_reached(self, monkeypatch):
        monkeypatch.setattr(settings, "CUSTOMER_DAILY_MSG_CAP", 30)
        db = AsyncMock()
        db.scalar.return_value = 30
        status = await check_budget("cust_1", db)
        assert status.over_customer_cap is True
        assert status.customer_calls_today == 30

    @pytest.mark.asyncio
    async def test_customer_cap_skipped_without_customer_id(self, monkeypatch):
        monkeypatch.setattr(settings, "CUSTOMER_DAILY_MSG_CAP", 30)
        db = AsyncMock()
        status = await check_budget(None, db)
        assert status.over_customer_cap is False
        db.scalar.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_db_error_fails_open(self, monkeypatch):
        """A broken meter must never stop sales — both guards fail open."""
        monkeypatch.setattr(settings, "DAILY_COST_LIMIT_USD", 5.0)
        monkeypatch.setattr(settings, "CUSTOMER_DAILY_MSG_CAP", 30)
        db = AsyncMock()
        db.scalar.side_effect = RuntimeError("connection lost")
        status = await check_budget("cust_1", db)
        assert status.over_daily_budget is False
        assert status.over_customer_cap is False


class TestCostReport:
    @pytest.mark.asyncio
    async def test_invalid_group_by_raises(self):
        with pytest.raises(ValueError, match="group_by"):
            await cost_report(AsyncMock(), group_by="hour")

    @pytest.mark.asyncio
    async def test_aggregates_rows_and_totals(self):
        rows = [
            SimpleNamespace(
                group_key="economy-chat",
                calls=8,
                prompt_tokens=800,
                completion_tokens=200,
                total_tokens=1000,
                cost_usd=0.02,
                latency_p50_ms=120.0,
                latency_p95_ms=480.0,
                cache_hits=2,
            ),
            SimpleNamespace(
                group_key="light-chat",
                calls=2,
                prompt_tokens=100,
                completion_tokens=50,
                total_tokens=150,
                cost_usd=0.0,
                latency_p50_ms=None,
                latency_p95_ms=None,
                cache_hits=0,
            ),
        ]
        db = AsyncMock()
        db.execute.return_value = MagicMock(all=MagicMock(return_value=rows))

        report = await cost_report(db, group_by="model")

        assert report["group_by"] == "model"
        assert report["totals"]["calls"] == 10
        assert report["totals"]["total_tokens"] == 1150
        assert report["totals"]["cost_usd"] == pytest.approx(0.02)
        assert report["totals"]["cache_hit_rate"] == pytest.approx(0.2)
        assert report["groups"][0]["cache_hit_rate"] == pytest.approx(0.25)
        assert report["groups"][1]["latency_p50_ms"] is None

    @pytest.mark.asyncio
    async def test_empty_range_zero_totals(self):
        db = AsyncMock()
        db.execute.return_value = MagicMock(all=MagicMock(return_value=[]))
        report = await cost_report(db, group_by="day")
        assert report["totals"] == {
            "calls": 0,
            "total_tokens": 0,
            "cost_usd": 0.0,
            "cache_hits": 0,
            "cache_hit_rate": 0.0,
        }
        assert report["groups"] == []


class TestAnswerPathBudgetGuard:
    """answer_node Path 3 integration: cap message, downgrade, cheap routing."""

    def _state(self, intent: str = "PRICING", model: str | None = None) -> dict:
        return {
            "session_id": "s1",
            "customer_id": "cust_1",
            "user_message": "giá con chuột này bao nhiêu",
            "intent": intent,
            "model_used": model,
            "retrieved_chunks": [],
            "citations": [],
        }

    @pytest.mark.asyncio
    async def test_customer_cap_returns_polite_message_without_llm(self):
        from core.agent.nodes.answer import CUSTOMER_CAP_MESSAGE, answer_node

        db = AsyncMock()
        with (
            patch(
                "services.costs.check_budget",
                AsyncMock(return_value=BudgetStatus(over_customer_cap=True)),
            ),
            patch("core.agent.nodes.answer.AIGateway.complete", AsyncMock()) as llm,
        ):
            result = await answer_node(self._state(), {"configurable": {"db": db}})

        assert result["response"] == CUSTOMER_CAP_MESSAGE
        assert result["model_used"] is None
        llm.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_over_budget_downgrades_to_light_chat(self):
        from core.agent.nodes.answer import answer_node

        db = AsyncMock()
        llm = AsyncMock(return_value=_llm_response())
        with (
            patch(
                "services.costs.check_budget",
                AsyncMock(return_value=BudgetStatus(over_daily_budget=True, daily_cost_usd=9.9)),
            ),
            patch("core.agent.nodes.answer.AIGateway.complete", llm),
        ):
            result = await answer_node(
                self._state(model="premium-chat"), {"configurable": {"db": db}}
            )

        assert llm.await_args.kwargs["model"] == "light-chat"
        assert result["model_used"] == "light-chat"

    @pytest.mark.asyncio
    async def test_smalltalk_routes_to_light_chat(self):
        from core.agent.nodes.answer import answer_node

        db = AsyncMock()
        llm = AsyncMock(return_value=_llm_response("Chào bạn!"))
        with (
            patch("services.costs.check_budget", AsyncMock(return_value=BudgetStatus())),
            patch("core.agent.nodes.answer.AIGateway.complete", llm),
        ):
            state = self._state(intent="SMALLTALK")
            state["user_message"] = "xin chào"
            result = await answer_node(state, {"configurable": {"db": db}})

        assert llm.await_args.kwargs["model"] == "light-chat"
        assert result["model_used"] == "light-chat"

    @pytest.mark.asyncio
    async def test_smalltalk_routing_kill_switch(self, monkeypatch):
        from core.agent.nodes.answer import answer_node

        monkeypatch.setattr(settings, "CHEAP_INTENT_LIGHT_ROUTING", False)
        db = AsyncMock()
        llm = AsyncMock(return_value=_llm_response("Chào bạn!"))
        with (
            patch("services.costs.check_budget", AsyncMock(return_value=BudgetStatus())),
            patch("core.agent.nodes.answer.AIGateway.complete", llm),
        ):
            state = self._state(intent="SMALLTALK")
            await answer_node(state, {"configurable": {"db": db}})

        assert llm.await_args.kwargs["model"] == "economy-chat"

    @pytest.mark.asyncio
    async def test_trace_metadata_stamped_with_customer_identity(self):
        """WP-V2-5: every trace row carries customer_id/session_id/intent in
        metadata so /admin/costs can group by customer."""
        from core.agent.nodes.answer import answer_node

        db = AsyncMock()
        llm = AsyncMock(return_value=_llm_response())
        with (
            patch("services.costs.check_budget", AsyncMock(return_value=BudgetStatus())),
            patch("core.agent.nodes.answer.AIGateway.complete", llm),
        ):
            await answer_node(self._state(), {"configurable": {"db": db}})

        stmt = db.execute.await_args[0][0]
        params = stmt.compile().params
        assert params["metadata"]["customer_id"] == "cust_1"
        assert params["metadata"]["session_id"] == "s1"
        assert params["metadata"]["intent"] == "PRICING"
