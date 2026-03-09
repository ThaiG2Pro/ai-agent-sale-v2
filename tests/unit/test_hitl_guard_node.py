"""Unit tests for hitl_guard_node (T058, T059)."""

from unittest.mock import AsyncMock, patch

import pytest
from langgraph.types import Command

from core.agent.nodes.hitl_guard import hitl_guard_node
from core.agent.state import HITLReasonEnum


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def mock_config(mock_db):
    return {"configurable": {"db": mock_db}}


@pytest.fixture
def initial_state():
    return {
        "session_id": "test-session",
        "intent": "INFO_QUERY",
        "confidence_score": 0.8,
        "hitl_approved": False,
        "hitl_escalation_count": 0,
        "messages": [],
    }


@pytest.mark.asyncio
async def test_hitl_guard_pass_through(initial_state, mock_config):
    """Test that node passes through when thresholds are not reached."""
    with patch("litellm.token_counter", return_value=100):
        result = await hitl_guard_node(initial_state, mock_config)
        assert isinstance(result, Command)
        assert result.goto == "answer_node"


@pytest.mark.asyncio
async def test_hitl_guard_triggers_on_low_confidence(initial_state, mock_config, mock_db):
    """Test that interrupt is called on low confidence (T058)."""
    initial_state["confidence_score"] = 0.5

    with patch(
        "core.agent.nodes.hitl_guard.interrupt",
        return_value={"action": "approve", "admin_user_id": "admin1"},
    ) as mock_interrupt:
        with patch("litellm.token_counter", return_value=100):
            result = await hitl_guard_node(initial_state, mock_config)

            # Verify interrupt called
            mock_interrupt.assert_called_once()

            # Verify DB calls
            assert mock_db.add.called
            mock_db.execute.assert_called()
            mock_db.commit.assert_called()

            # Verify resume path (T059)
            assert result.goto == "queue_consumer_node"
            assert result.update["hitl_approved"] is True


@pytest.mark.asyncio
async def test_hitl_guard_triggers_on_order_placement(initial_state, mock_config, mock_db):
    """Test that interrupt is called for ORDER_PLACEMENT regardless of confidence."""
    initial_state["intent"] = "ORDER_PLACEMENT"
    initial_state["confidence_score"] = 0.9

    with patch(
        "core.agent.nodes.hitl_guard.interrupt",
        return_value={"action": "approve", "admin_user_id": "admin1"},
    ) as mock_interrupt:
        await hitl_guard_node(initial_state, mock_config)
        mock_interrupt.assert_called_once()
        assert mock_interrupt.call_args[0][0]["reason"] == HITLReasonEnum.ORDER_APPROVAL


@pytest.mark.asyncio
async def test_hitl_guard_overflow_protection(initial_state, mock_config):
    """Test overflow guard when max escalation is reached (T058)."""
    initial_state["confidence_score"] = 0.5
    initial_state["hitl_escalation_count"] = 2  # max is 2

    with patch("core.agent.nodes.hitl_guard.interrupt") as mock_interrupt:
        result = await hitl_guard_node(initial_state, mock_config)

        # Should NOT call interrupt
        mock_interrupt.assert_not_called()

        # Should route to customer_support_node
        assert result.goto == "customer_support_node"
        assert result.update["hitl_rejection_reason"] == "max_escalation_reached"


@pytest.mark.asyncio
async def test_hitl_guard_reject_resume(initial_state, mock_config):
    """Test resume path with rejection (T059)."""
    initial_state["confidence_score"] = 0.5

    resume_payload = {
        "action": "reject",
        "admin_user_id": "admin1",
        "reason_or_comment": "suspicious request",
    }

    with patch("core.agent.nodes.hitl_guard.interrupt", return_value=resume_payload):
        with patch("litellm.token_counter", return_value=100):
            result = await hitl_guard_node(initial_state, mock_config)

            assert result.goto == "customer_support_node"
            assert result.update["hitl_rejection_reason"] == "suspicious request"
            assert result.update["hitl_escalation_count"] == 1
