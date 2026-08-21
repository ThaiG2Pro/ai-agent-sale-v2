"""Unit tests for order_execution_node business paths (Phase 11, T038 + SC5).

Complements test_order_execution_timeout.py (timeout wrapper path) with the
core behaviors: input guard, stock race, happy path, and the SC5
pending-INFO-questions append (both LLM success and LLM failure).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agent.nodes.order_execution import order_execution_node


def _state(**extra) -> dict:
    return {
        "session_id": "sess-1",
        "customer_id": "cust-1",
        "order_info": {"product_id": "p1", "quantity": 2, "name": "Widget"},
        **extra,
    }


def _db(rowcount: int = 1) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.rowcount = rowcount
    db.execute.return_value = result
    return db


def _config(db: AsyncMock) -> dict:
    return {"configurable": {"db": db}}


@pytest.mark.asyncio
async def test_missing_order_info_returns_error():
    cmd = await order_execution_node({"session_id": "s1"}, _config(_db()))
    assert cmd.goto == "answer_node"
    assert cmd.update["error"] == "Missing order information"


@pytest.mark.asyncio
async def test_stock_exhaustion_routes_to_customer_support():
    """rowcount 0 = stock guard rejected the decrement (race or sold out)."""
    cmd = await order_execution_node(_state(), _config(_db(rowcount=0)))
    assert cmd.goto == "customer_support_node"
    assert cmd.update["hitl_rejection_reason"] == "out_of_stock_last_minute"


@pytest.mark.asyncio
async def test_successful_order_confirms_and_flushes():
    db = _db(rowcount=1)
    cmd = await order_execution_node(_state(), _config(db))

    assert cmd.goto == "answer_node"
    assert cmd.update["order_info"]["status"] == "confirmed"
    assert "Widget" in cmd.update["response"]
    assert "Số lượng: 2" in cmd.update["response"]
    # stock UPDATE + order INSERT + semantic-cache invalidation (v3-0 P4 4.3:
    # stock changed → cached availability/pricing answers are stale)
    assert db.execute.await_count == 3
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_pending_info_questions_appended_to_confirmation():
    """SC5: INFO questions asked while waiting are answered after confirmation."""
    llm_resp = MagicMock()
    llm_resp.choices[0].message.content = "Bảo hành 12 tháng."

    with patch("litellm.acompletion", AsyncMock(return_value=llm_resp)):
        cmd = await order_execution_node(
            _state(
                pending_info_questions="Bảo hành bao lâu?",
                citations=[{"name": "Widget", "description": "Đồ chơi"}],
            ),
            _config(_db()),
        )

    assert "Bảo hành 12 tháng." in cmd.update["response"]
    assert cmd.update["pending_info_questions"] is None


@pytest.mark.asyncio
async def test_pending_info_llm_failure_keeps_confirmation():
    """SC5 LLM failure must not break the order confirmation itself."""
    with patch("litellm.acompletion", AsyncMock(side_effect=RuntimeError("offline"))):
        cmd = await order_execution_node(
            _state(pending_info_questions="Ship mấy ngày?"),
            _config(_db()),
        )

    assert cmd.goto == "answer_node"
    assert "Đặt hàng thành công" in cmd.update["response"]
    assert cmd.update["order_info"]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_db_exception_surfaces_as_error_response():
    """DB exceptions are captured by the timeout wrapper and returned as an
    error update — never raised out of the node."""
    db = AsyncMock()
    db.execute.side_effect = RuntimeError("connection lost")
    cmd = await order_execution_node(_state(), _config(db))

    assert cmd.goto == "answer_node"
    assert "connection lost" in cmd.update["error"]
