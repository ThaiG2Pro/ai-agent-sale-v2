"""Unit tests for queue_consumer_node (T060, T061)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from core.agent.nodes.queue_consumer import queue_consumer_node
from services.hitl.schemas import QueuedMessageBatch, QueueIntentResult


@pytest.fixture
def mock_db():
    mock = AsyncMock()
    # Mock return value for scalars().all()
    mock_result = MagicMock()
    mock_result.scalars().all.return_value = []
    mock.execute.return_value = mock_result
    return mock


@pytest.fixture
def mock_config(mock_db):
    return {"configurable": {"db": mock_db}}


@pytest.fixture
def initial_state():
    return {
        "session_id": "test-session",
        "messages": [],
        "hitl_approved": True,
        "hitl_escalation_count": 0,
    }


@pytest.mark.asyncio
async def test_queue_consumer_orphan_tool_scan(initial_state, mock_config):
    """Test that orphan tool calls are closed (T060)."""
    initial_state["messages"] = [
        AIMessage(
            content="using tool", tool_calls=[{"id": "call_1", "name": "get_product", "args": {}}]
        )
    ]

    result = await queue_consumer_node(initial_state, mock_config)

    messages = result.update["messages"]
    assert len(messages) == 2
    assert isinstance(messages[1], ToolMessage)
    assert messages[1].tool_call_id == "call_1"
    assert "cancelled" in messages[1].content


@pytest.mark.asyncio
async def test_queue_consumer_empty_queue(initial_state, mock_config, mock_db):
    """Test path with no queued messages (T060)."""
    result = await queue_consumer_node(initial_state, mock_config)

    assert result.goto == "state_freshness_validator_node"
    assert "messages" not in result.update  # No humans messages added


@pytest.mark.asyncio
async def test_queue_consumer_cancel_override(initial_state, mock_config, mock_db):
    """Test that CANCEL in queue overrides admin approval (T061)."""
    # 1. Mock DB to return one message
    mock_msg = MagicMock()
    mock_msg.message_id = "msg_1"
    mock_msg.message_text = "Stop everything"

    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [mock_msg]
    mock_db.execute.return_value = mock_result

    # 2. Mock LiteLLM to return CANCEL intent
    batch_result = QueuedMessageBatch(
        session_id="test-session",
        messages=[
            QueueIntentResult(message_id="msg_1", text="Stop", intent="CANCEL", confidence=0.9)
        ],
    )

    mock_response = MagicMock()
    mock_response.choices[0].message.content = batch_result

    with patch("litellm.acompletion", return_value=mock_response):
        result = await queue_consumer_node(initial_state, mock_config)

        assert result.goto == "cancellation_node"
        assert len(result.update["messages"]) == 1  # The enqueued human message


@pytest.mark.asyncio
async def test_queue_consumer_modify_re_pause(initial_state, mock_config, mock_db):
    """Test that MODIFY intent causes re-pause (T061)."""
    # 1. Mock DB
    mock_msg = MagicMock()
    mock_msg.message_id = "msg_1"
    mock_msg.message_text = "I want 2 instead of 1"

    mock_result = MagicMock()
    mock_result.scalars().all.return_value = [mock_msg]
    mock_db.execute.return_value = mock_result

    # 2. Mock LiteLLM to return MODIFY_ORDER
    batch_result = QueuedMessageBatch(
        session_id="test-session",
        messages=[
            QueueIntentResult(message_id="msg_1", text="2", intent="MODIFY_ORDER", confidence=0.9)
        ],
    )

    mock_response = MagicMock()
    mock_response.choices[0].message.content = batch_result

    with patch("litellm.acompletion", return_value=mock_response):
        result = await queue_consumer_node(initial_state, mock_config)

        assert result.goto == "hitl_guard_node"
        assert result.update["hitl_escalation_count"] == 1
        assert result.update["hitl_triggered"] is False  # Reset for hitl_guard to re-trigger
