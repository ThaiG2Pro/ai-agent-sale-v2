"""Unit tests for hitl_guard_node (T058, T059)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.types import Command

from core.agent.nodes.hitl_guard import hitl_guard_node
from core.agent.state import HITLReasonEnum


@pytest.fixture
def mock_db():
    db = AsyncMock()
    # scalar_one_or_none is sync on SQLAlchemy result; return None = no existing record
    db.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)
    return db


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
    """Test that interrupt is called for ORDER_PLACEMENT when order_info is resolved."""
    initial_state["intent"] = "ORDER_PLACEMENT"
    initial_state["confidence_score"] = 0.9
    # order_info must be set (product found by confidence_node); without it the guard
    # routes to answer_node with "product not found" instead of triggering HITL.
    initial_state["order_info"] = {
        "product_id": "test-product-id",
        "sku": "TEST-001",
        "name": "Test Product",
        "price": 10000000.0,
        "approved_price": 10000000.0,
        "quantity": 1,
        "status": "pending",
    }

    with patch(
        "core.agent.nodes.hitl_guard.interrupt",
        return_value={"action": "approve", "admin_user_id": "admin1"},
    ) as mock_interrupt:
        await hitl_guard_node(initial_state, mock_config)
        mock_interrupt.assert_called_once()
        assert mock_interrupt.call_args[0][0]["reason"] == HITLReasonEnum.ORDER_APPROVAL


@pytest.mark.asyncio
async def test_hitl_guard_order_placement_no_product_found(initial_state, mock_config):
    """Test that ORDER_PLACEMENT without order_info routes to answer_node (product not found)."""
    initial_state["intent"] = "ORDER_PLACEMENT"
    initial_state["confidence_score"] = 0.9
    # order_info is NOT set — product not found in catalog

    with patch("core.agent.nodes.hitl_guard.interrupt") as mock_interrupt:
        result = await hitl_guard_node(initial_state, mock_config)

        # interrupt must NOT be called — no point pausing human if product unknown
        mock_interrupt.assert_not_called()
        assert result.goto == "answer_node"


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
async def test_hitl_guard_resume_reuses_pause_id_without_new_records(initial_state, mock_db):
    """V3-5: resume detection contract — an existing paused/resuming HITLMetadata
    row means LangGraph is re-running the node after service.py resume; the node
    must reuse that pause_id and must NOT insert duplicate pause records."""
    initial_state["confidence_score"] = 0.5

    existing = MagicMock()
    existing.pause_id = "existing-pause-id"
    mock_db.execute.return_value.scalar_one_or_none = MagicMock(return_value=existing)
    config = {"configurable": {"db": mock_db}}

    with patch(
        "core.agent.nodes.hitl_guard.interrupt",
        return_value={"action": "approve", "admin_user_id": "admin1"},
    ) as mock_interrupt:
        with patch("litellm.token_counter", return_value=100):
            result = await hitl_guard_node(initial_state, config)

    # Reuses the pause_id from the DB record, end to end
    assert mock_interrupt.call_args[0][0]["pause_id"] == "existing-pause-id"
    assert result.update["hitl_pause_id"] == "existing-pause-id"
    # No duplicate HITLMetadata / InterruptedSession records on resume
    mock_db.add.assert_not_called()


@pytest.mark.asyncio
async def test_hitl_guard_fresh_trigger_creates_pause_records(initial_state, mock_config, mock_db):
    """V3-5: fresh-trigger contract — no paused/resuming row in DB means this is
    a brand-new pause; the node must create HITLMetadata + InterruptedSession and
    commit BEFORE interrupt() suspends execution (else the admin API can't see it)."""
    initial_state["confidence_score"] = 0.5

    with patch(
        "core.agent.nodes.hitl_guard.interrupt",
        return_value={"action": "approve", "admin_user_id": "admin1"},
    ):
        with patch("litellm.token_counter", return_value=100):
            result = await hitl_guard_node(initial_state, mock_config)

    # New HITLMetadata added and persisted before the graph suspends
    mock_db.add.assert_called_once()
    added = mock_db.add.call_args[0][0]
    assert added.status == "paused"
    assert added.session_id == "test-session"
    mock_db.commit.assert_called()
    # The freshly minted pause_id flows through to the resume update
    assert result.update["hitl_pause_id"] == str(added.pause_id)


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
