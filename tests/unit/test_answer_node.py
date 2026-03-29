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
    state = make_initial_state(
        user_message="what is the price?",
        session_id="t001",
        customer_id="cust_001",
    )
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
    state = make_initial_state(
        user_message="what is the price?",
        session_id="t001",
        customer_id="cust_001",
    )
    state["declined"] = True
    state["escalation_flag"] = False  # must stay False

    config = {"configurable": {}}
    with patch("services.ai.ai_router.acompletion", new_callable=AsyncMock):
        with patch("core.agent.nodes.answer._write_model_trace", new_callable=AsyncMock):
            result = await answer_node(state, config)

    # escalation_flag not returned (not touched) on declined path
    assert "escalation_flag" not in result or result.get("escalation_flag") is False
    assert result["response"] == DECLINE_MESSAGE


# Week 5: Memory Context Injection Tests (T087-T089)


@pytest.mark.asyncio
async def test_answer_node_with_memory_context_non_empty():
    """T087: answer_node with memory_context → system prompt includes past context."""
    state = make_initial_state(
        user_message="What's the price?",
        session_id="t001",
        customer_id="cust_001",
    )
    state["intent"] = "PRICING"
    state["retrieved_chunks"] = []  # No product chunks
    state["model_used"] = "economy-chat"
    state["memory_context"] = [
        {"summary": "Customer asked about laptop prices last week"},
        {"summary": "Customer interested in gaming features"},
    ]

    config = {"configurable": {}}
    with patch("services.ai.AIGateway.complete", new_callable=AsyncMock) as mock_llm:
        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock(message=AsyncMock(content="Answer from LLM"))]
        mock_llm.return_value = mock_response

        with patch("core.agent.nodes.answer._write_model_trace", new_callable=AsyncMock):
            await answer_node(state, config)

    # Verify the system prompt call includes memory context
    mock_llm.assert_called_once()
    call_args = mock_llm.call_args
    messages = call_args.kwargs.get("messages", [])
    system_content = next((m["content"] for m in messages if m.get("role") == "system"), None)

    assert system_content is not None
    assert "ngữ cảnh từ các cuộc hội thoại trước" in system_content.lower()
    assert "laptop prices" in system_content


@pytest.mark.asyncio
async def test_answer_node_with_empty_memory_context():
    """T088: empty memory_context → no past context block (cold start)."""
    state = make_initial_state(
        user_message="What's the price?",
        session_id="t001",
        customer_id="cust_001",
    )
    state["intent"] = "PRICING"
    state["retrieved_chunks"] = []
    state["model_used"] = "economy-chat"
    state["memory_context"] = []  # Empty — cold start
    state["declined"] = False
    state["response"] = None  # Explicitly set to None
    state["cached_answer"] = None  # Explicitly set to None

    config = {"configurable": {}}
    with patch("services.ai.AIGateway.complete", new_callable=AsyncMock) as mock_llm:
        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock(message=AsyncMock(content="Cold start answer"))]
        mock_llm.return_value = mock_response

        with patch("core.agent.nodes.answer._write_model_trace", new_callable=AsyncMock):
            result = await answer_node(state, config)

    # Verify response was generated
    assert result.get("response") == "Cold start answer"
    # Verify LLM was called
    assert mock_llm.called
    # Verify the system prompt does NOT include memory context
    call_args = mock_llm.call_args
    messages = call_args.kwargs.get("messages", [])
    system_content = next((m["content"] for m in messages if m.get("role") == "system"), None)

    assert system_content is not None
    assert "ngữ cảnh từ các cuộc hội thoại trước" not in system_content.lower()


@pytest.mark.asyncio
async def test_answer_node_with_multiple_memory_entries():
    """T089: multiple memory entries → all appear in system prompt."""
    state = make_initial_state(
        user_message="What's new?",
        session_id="t001",
        customer_id="cust_001",
    )
    state["intent"] = "PRICING"
    state["retrieved_chunks"] = []
    state["model_used"] = "economy-chat"
    state["memory_context"] = [
        {"summary": "Customer bought a monitor 2 weeks ago"},
        {"summary": "Customer complained about keyboard latency last month"},
        {"summary": "Customer interested in mechanical switches"},
    ]

    config = {"configurable": {}}
    with patch("services.ai.AIGateway.complete", new_callable=AsyncMock) as mock_llm:
        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock(message=AsyncMock(content="Multi-entry answer"))]
        mock_llm.return_value = mock_response

        with patch("core.agent.nodes.answer._write_model_trace", new_callable=AsyncMock):
            await answer_node(state, config)

    # Verify all three memory entries are in the system prompt
    call_args = mock_llm.call_args
    messages = call_args.kwargs.get("messages", [])
    system_content = next((m["content"] for m in messages if m.get("role") == "system"), None)

    assert system_content is not None
    assert "monitor" in system_content
    assert "keyboard latency" in system_content
    assert "mechanical switches" in system_content


# Week 5: Context Compression Tests (T109-T110)


@pytest.mark.asyncio
async def test_answer_node_compression_with_summary_exists():
    """T109: thread_summary_exists=True → compress to summary + last 5 messages."""
    state = make_initial_state(
        user_message="Any updates?",
        session_id="t001",
        customer_id="cust_001",
    )
    state["intent"] = "PRICING"
    state["retrieved_chunks"] = []
    state["model_used"] = "economy-chat"
    state["thread_summary_exists"] = True
    # Simulate 25 memory entries (summary + 24 recent messages)
    state["memory_context"] = (
        [{"summary": "Overall conversation summary: customer interested in laptops"}]
        + [{"text": f"Old message {i}"} for i in range(1, 20)]
        + [{"text": f"Recent message {i}"} for i in range(20, 26)]
    )

    config = {"configurable": {}}
    with patch("services.ai.AIGateway.complete", new_callable=AsyncMock) as mock_llm:
        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock(message=AsyncMock(content="Compressed answer"))]
        mock_llm.return_value = mock_response

        with patch("core.agent.nodes.answer._write_model_trace", new_callable=AsyncMock):
            await answer_node(state, config)

    # Verify LLM was called with compressed context (≤ 6 items: 1 summary + 5 recent)
    call_args = mock_llm.call_args
    messages = call_args.kwargs.get("messages", [])
    system_content = next((m["content"] for m in messages if m.get("role") == "system"), None)

    assert system_content is not None
    # Count context items - should have summary + last 5 messages only
    context_lines = [
        line
        for line in system_content.split("\n")
        if line.strip().startswith("📋") or line.strip().startswith("-")
    ]
    assert len(context_lines) <= 6, f"Expected ≤ 6 context items, got {len(context_lines)}"
    # Verify recent messages are included
    assert "Recent message" in system_content


@pytest.mark.asyncio
async def test_answer_node_no_compression_without_summary():
    """T110: thread_summary_exists=False → include all 5 memory entries (no compression)."""
    state = make_initial_state(
        user_message="What do you have?",
        session_id="t001",
        customer_id="cust_001",
    )
    state["intent"] = "INFO_QUERY"
    state["retrieved_chunks"] = []
    state["model_used"] = "economy-chat"
    state["thread_summary_exists"] = False
    state["memory_context"] = [{"text": f"Memory entry {i}"} for i in range(1, 6)]  # 5 entries

    config = {"configurable": {}}
    with patch("services.ai.AIGateway.complete", new_callable=AsyncMock) as mock_llm:
        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock(message=AsyncMock(content="No compression answer"))]
        mock_llm.return_value = mock_response

        with patch("core.agent.nodes.answer._write_model_trace", new_callable=AsyncMock):
            await answer_node(state, config)

    # Verify all 5 memory entries are in system prompt (no compression)
    call_args = mock_llm.call_args
    messages = call_args.kwargs.get("messages", [])
    system_content = next((m["content"] for m in messages if m.get("role") == "system"), None)

    assert system_content is not None
    for i in range(1, 6):
        assert f"Memory entry {i}" in system_content
