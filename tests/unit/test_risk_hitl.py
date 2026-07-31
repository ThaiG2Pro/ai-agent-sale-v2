"""WP-V2-4 unit tests — risk-score HITL tiers in hitl_guard_node.

Covers the risk matrix (order value x confidence x history), the safety
invariant (high-value / unknown-value orders never auto-approve), Tier1
auto-approval, Tier3 direct escalation, and the RISK_HITL_ENABLED kill switch.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.types import Command

from core.agent.nodes.hitl_guard import (
    _history_factor,
    _order_value,
    _resolve_risk_tier,
    _risk_score,
    _value_norm,
    hitl_guard_node,
)
from core.agent.state import HITLReasonEnum
from models.schema import IntentStatus


def _make_db(statuses: list[str] | None = None):
    """AsyncSession mock: intent_tracking status select + HITL metadata select."""
    db = AsyncMock()

    def execute(*args, **kwargs):
        result = MagicMock()
        result.scalars.return_value.all.return_value = statuses or []
        result.scalar_one_or_none = MagicMock(return_value=None)
        return result

    db.execute = AsyncMock(side_effect=execute)
    return db


def _config(db):
    return {"configurable": {"db": db}}


def _order_state(price: float, confidence: float = 0.9, customer_id: str = "cust_1") -> dict:
    return {
        "session_id": "s1",
        "intent": "ORDER_PLACEMENT",
        "confidence_score": confidence,
        "customer_id": customer_id,
        "hitl_approved": False,
        "hitl_escalation_count": 0,
        "messages": [],
        "order_info": {
            "product_id": "p1",
            "sku": "SKU-1",
            "name": "Chuột không dây",
            "price": price,
            "approved_price": price,
            "quantity": 1,
            "status": "pending",
        },
    }


# ── Pure helpers: risk matrix ───────────────────────────────────────────────


class TestRiskMatrix:
    def test_order_value_from_order_info(self):
        assert _order_value({"price": 100_000.0, "quantity": 2}) == 200_000.0
        assert _order_value({"price": 100_000.0}) == 100_000.0  # quantity defaults 1

    def test_order_value_missing_is_none(self):
        assert _order_value(None) is None
        assert _order_value({}) is None
        assert _order_value({"price": None}) is None
        assert _order_value({"price": "abc"}) is None
        assert _order_value({"price": 0}) is None  # zero = undetermined

    def test_value_norm_only_for_orders(self):
        # Non-order intents carry no value risk — even with no order_info.
        assert _value_norm("INFO_QUERY", None) == 0.0
        # Missing value on an ORDER is MAX risk (conservative).
        assert _value_norm("ORDER_PLACEMENT", None) == 1.0
        # Value normalizes against the cap and clamps at 1.0.
        assert _value_norm("ORDER_PLACEMENT", 10_000_000.0) == pytest.approx(0.5)
        assert _value_norm("ORDER_PLACEMENT", 100_000_000.0) == 1.0

    def test_risk_score_weights(self):
        # conf=0.9, value=0, history=0 → only the confidence term remains
        assert _risk_score(0.9, 0.0, 0.0) == pytest.approx(0.4 * 0.1)
        # worst case everything → sum of weights = 1.0
        assert _risk_score(0.0, 1.0, 1.0) == pytest.approx(1.0)

    def test_tier_mapping(self):
        assert _resolve_risk_tier("INFO_QUERY", None, 0.1) == 1
        assert _resolve_risk_tier("INFO_QUERY", None, 0.5) == 2
        assert _resolve_risk_tier("INFO_QUERY", None, 0.8) == 3

    def test_safety_invariant_high_value_never_tier1(self):
        # Order above HITL_HIGH_VALUE_ORDER_THRESHOLD (5M) with a LOW risk
        # score is still forced to Tier 2.
        assert _resolve_risk_tier("ORDER_PLACEMENT", 10_000_000.0, 0.05) == 2

    def test_safety_invariant_unknown_value_never_tier1(self):
        assert _resolve_risk_tier("ORDER_PLACEMENT", None, 0.05) == 2

    def test_small_order_low_risk_is_tier1(self):
        assert _resolve_risk_tier("ORDER_PLACEMENT", 200_000.0, 0.1) == 1

    def test_tier3_still_possible_for_orders(self):
        assert _resolve_risk_tier("ORDER_PLACEMENT", 10_000_000.0, 0.9) == 3


class TestHistoryFactor:
    @pytest.mark.asyncio
    async def test_no_customer_id_is_max_risk(self):
        assert await _history_factor(None, _make_db()) == 1.0
        assert await _history_factor("cust_1", None) == 1.0

    @pytest.mark.asyncio
    async def test_unknown_customer_is_max_risk(self):
        assert await _history_factor("cust_new", _make_db([])) == 1.0

    @pytest.mark.asyncio
    async def test_converted_customer_is_lowest_risk(self):
        db = _make_db([IntentStatus.CONVERTED, IntentStatus.ENGAGED])
        assert await _history_factor("cust_vip", db) == 0.0

    @pytest.mark.asyncio
    async def test_engaged_customer_is_medium_risk(self):
        assert await _history_factor("cust_eng", _make_db([IntentStatus.ENGAGED])) == 0.5

    @pytest.mark.asyncio
    async def test_new_only_customer_is_high_risk(self):
        assert await _history_factor("cust_n", _make_db([IntentStatus.NEW])) == 0.8

    @pytest.mark.asyncio
    async def test_db_error_is_max_risk(self):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=RuntimeError("db down"))
        assert await _history_factor("cust_1", db) == 1.0


# ── Node behavior ───────────────────────────────────────────────────────────


class TestHitlGuardTiers:
    @pytest.mark.asyncio
    async def test_small_order_known_customer_auto_approves(self):
        """Tier 1: small order + CONVERTED customer + high confidence →
        auto-proceed to queue_consumer_node without interrupt."""
        db = _make_db([IntentStatus.CONVERTED])
        state = _order_state(price=200_000.0, confidence=0.95)

        with patch("core.agent.nodes.hitl_guard.interrupt") as mock_interrupt:
            with patch("litellm.token_counter", return_value=100):
                result = await hitl_guard_node(state, _config(db))

        mock_interrupt.assert_not_called()
        assert isinstance(result, Command)
        assert result.goto == "queue_consumer_node"
        assert result.update["hitl_approved"] is True
        assert result.update["order_info"]["sku"] == "SKU-1"

    @pytest.mark.asyncio
    async def test_big_order_new_customer_interrupts(self):
        """Safety invariant: order above the high-value threshold interrupts
        (>= Tier 2) even for a known customer with high confidence."""
        db = _make_db([IntentStatus.CONVERTED])
        state = _order_state(price=10_000_000.0, confidence=0.95)

        with patch(
            "core.agent.nodes.hitl_guard.interrupt",
            return_value={"action": "approve", "admin_user_id": "admin1"},
        ) as mock_interrupt:
            await hitl_guard_node(state, _config(db))

        mock_interrupt.assert_called_once()
        assert mock_interrupt.call_args[0][0]["reason"] == HITLReasonEnum.ORDER_APPROVAL

    @pytest.mark.asyncio
    async def test_missing_order_value_interrupts(self):
        """Safety invariant: unparseable order value = high risk → interrupt."""
        db = _make_db([IntentStatus.CONVERTED])
        state = _order_state(price=200_000.0, confidence=0.95)
        state["order_info"]["price"] = None

        with patch(
            "core.agent.nodes.hitl_guard.interrupt",
            return_value={"action": "approve", "admin_user_id": "admin1"},
        ) as mock_interrupt:
            await hitl_guard_node(state, _config(db))

        mock_interrupt.assert_called_once()

    @pytest.mark.asyncio
    async def test_tier3_goes_straight_to_support(self):
        """Tier 3: worst-case risk (low confidence, big order, new customer)
        skips the interrupt and routes to the support queue."""
        db = _make_db([])  # unknown customer → history 1.0
        state = _order_state(price=50_000_000.0, confidence=0.1)
        # risk = 0.4*0.9 + 0.4*1.0 + 0.2*1.0 = 0.96 ≥ 0.75 → Tier 3

        with patch("core.agent.nodes.hitl_guard.interrupt") as mock_interrupt:
            result = await hitl_guard_node(state, _config(db))

        mock_interrupt.assert_not_called()
        assert result.goto == "customer_support_node"
        assert result.update["hitl_rejection_reason"] == "high_risk_tier3"

    @pytest.mark.asyncio
    async def test_non_order_high_confidence_passes_through(self):
        """Non-order intents carry no value risk — high confidence passes."""
        db = _make_db([])
        state = {
            "session_id": "s1",
            "intent": "INFO_QUERY",
            "confidence_score": 0.9,
            "customer_id": "cust_1",
            "hitl_approved": False,
            "hitl_escalation_count": 0,
            "messages": [],
        }

        with patch("core.agent.nodes.hitl_guard.interrupt") as mock_interrupt:
            with patch("litellm.token_counter", return_value=100):
                result = await hitl_guard_node(state, _config(db))

        mock_interrupt.assert_not_called()
        assert result.goto == "answer_node"

    @pytest.mark.asyncio
    async def test_kill_switch_restores_binary_triggers(self, monkeypatch):
        """RISK_HITL_ENABLED=False: every ORDER_PLACEMENT interrupts, even a
        tiny order from a CONVERTED customer (pre-V2-4 behavior)."""
        monkeypatch.setattr("core.agent.nodes.hitl_guard.settings.RISK_HITL_ENABLED", False)
        db = _make_db([IntentStatus.CONVERTED])
        state = _order_state(price=200_000.0, confidence=0.95)

        with patch(
            "core.agent.nodes.hitl_guard.interrupt",
            return_value={"action": "approve", "admin_user_id": "admin1"},
        ) as mock_interrupt:
            await hitl_guard_node(state, _config(db))

        mock_interrupt.assert_called_once()
