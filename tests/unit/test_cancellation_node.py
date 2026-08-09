"""Unit tests for cancellation_node (Phase 12).

Covers the two behaviors: order_info status flip to 'cancelled' when an
order exists, and graceful message-only response when it doesn't.
"""

import pytest
from langgraph.types import Command

from core.agent.nodes.cancellation import cancellation_node


@pytest.mark.asyncio
async def test_cancellation_marks_order_cancelled():
    state = {
        "session_id": "s1",
        "order_info": {"product_id": "p1", "name": "Widget", "status": "pending"},
    }

    result = await cancellation_node(state)

    assert isinstance(result, Command)
    assert result.goto == "answer_node"
    assert result.update["order_info"]["status"] == "cancelled"
    # Original fields preserved
    assert result.update["order_info"]["product_id"] == "p1"
    assert "hủy" in result.update["response"]


@pytest.mark.asyncio
async def test_cancellation_without_order_info_still_responds():
    state = {"session_id": "s1"}

    result = await cancellation_node(state)

    assert result.goto == "answer_node"
    assert "hủy" in result.update["response"]
    assert "order_info" not in result.update
