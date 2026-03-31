"""Unit tests for order_execution timeout wrapping (T093-T094)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.agent.nodes.order_execution import order_execution_node
from core.tools.models import ToolResult


@pytest.mark.asyncio
async def test_order_execution_returns_error_when_timeout_occurs() -> None:
    state = {
        "session_id": "sess-1",
        "customer_id": "cust-1",
        "order_info": {"product_id": "p1", "quantity": 1, "name": "Product A"},
    }
    db = AsyncMock()
    config = {"configurable": {"db": db}}

    timeout_result = ToolResult(
        success=False,
        data=None,
        error="order_processing timed out after 10.0s",
        is_retryable=True,
    )

    with patch(
        "core.agent.nodes.order_execution.wrap_tool_with_timeout",
        new=AsyncMock(return_value=timeout_result),
    ):
        cmd = await order_execution_node(state, config)

    assert cmd.update["error"] == "order_processing timed out after 10.0s"
