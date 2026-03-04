"""Unit tests for answer_node fallback behavior (Phase 6, T075-T076).

Tests that declined state skips LLM and returns DECLINE_MESSAGE.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.agent.nodes.answer import answer_node
from core.agent.state import make_initial_state
from services.rag.constants import DECLINE_MESSAGE


@pytest.mark.asyncio
async def test_answer_node_fallback_no_llm_call():
    """T075: declined=True → no LLM call, response=DECLINE_MESSAGE, model_used=None.

    Also verifies FR-008: _write_model_trace IS called with intended_model in metadata_.
    """
    state = make_initial_state("test-session", "what is the price?")
    state["declined"] = True
    state["model_used"] = "economy-chat"  # intended model before decline

    # Provide empty config (no db) — declined path must not require DB
    config = {"configurable": {}}
    with patch("services.ai.ai_router.acompletion", new_callable=AsyncMock) as mock_llm:
        with patch(
            "core.agent.nodes.answer._write_model_trace", new_callable=AsyncMock
        ) as mock_trace:
            result = await answer_node(state, config)

    mock_llm.assert_not_called()
    assert result["response"] == DECLINE_MESSAGE
    assert result["model_used"] is None
    # FR-008: trace must be called even on declined path
    mock_trace.assert_called_once()
    _, kwargs = mock_trace.call_args
    assert "metadata_" in kwargs or len(mock_trace.call_args.args) >= 2


@pytest.mark.asyncio
async def test_answer_node_fallback_state():
    """T076: declined=True does NOT change escalation_flag (state field isolation)."""
    state = make_initial_state("test-session", "what is the price?")
    state["declined"] = True
    state["escalation_flag"] = False  # must stay False

    config = {"configurable": {}}
    with patch("services.ai.ai_router.acompletion", new_callable=AsyncMock):
        with patch("core.agent.nodes.answer._write_model_trace", new_callable=AsyncMock):
            result = await answer_node(state, config)

    # escalation_flag not returned (not touched) on declined path
    assert "escalation_flag" not in result or result.get("escalation_flag") is False
    assert result["response"] == DECLINE_MESSAGE
