"""Unit tests for HITLService (T063)."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from services.hitl.schemas import ReviewActionCreate
from services.hitl.service import HITLService


@pytest.fixture
def mock_db():
    db = AsyncMock()

    # Configure execute to return a non-async mock result
    # This allows result.scalar_one_or_none() to return a value, not a coroutine
    mock_result = MagicMock()
    db.execute.return_value = mock_result

    return db


@pytest.fixture
def mock_graph():
    graph = AsyncMock()
    # Mock aget_state return
    state_snap = MagicMock()
    state_snap.values = {"messages": []}
    graph.aget_state.return_value = state_snap
    return graph


@pytest.fixture
def sample_payload():
    return ReviewActionCreate(
        session_id="session-123",
        pause_id=str(uuid.uuid4()),
        action="approve",
        expected_version=0,
        admin_user_id="admin-1",
    )


@pytest.mark.asyncio
async def test_hitl_service_idempotency_replay(mock_db, mock_graph, sample_payload):
    """Test that duplicate requests return cached result (T063)."""
    # 1. Idempotency check returns an existing action_id
    mock_db.execute.return_value.scalar_one_or_none.return_value = "existing-action-id"

    result = await HITLService.process_approve(sample_payload, "key-123", mock_db, mock_graph, {})

    assert result == {"status": "hit", "action_id": "existing-action-id"}
    assert not mock_graph.ainvoke.called


@pytest.mark.asyncio
async def test_hitl_service_optimistic_lock_conflict(mock_db, mock_graph, sample_payload):
    """Test that version mismatch raises 409 (T063)."""
    # 1. No idempotency hit
    # 2. Version increment returns 0 rows (conflict)
    # 3. get() for current version info
    mock_db.execute.return_value.scalar_one_or_none.return_value = None
    mock_db.execute.return_value.rowcount = 0
    mock_db.get.return_value = MagicMock(version=1)

    with pytest.raises(HTTPException) as exc:
        await HITLService.process_approve(sample_payload, "key-123", mock_db, mock_graph, {})

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_hitl_service_request_edit_no_resume(mock_db, mock_graph, sample_payload):
    """Test that request_edit updates state but doesn't resume graph (T063)."""
    sample_payload.action = "request_edit"
    sample_payload.state_edits = {"price": 100}

    # 1. No idempotency hit
    mock_db.execute.return_value.scalar_one_or_none.return_value = None
    # 2. Version increment success
    mock_db.execute.return_value.rowcount = 1

    result = await HITLService.process_request_edit(
        sample_payload, "key-123", mock_db, mock_graph, {}
    )

    assert result["status"] == "edit_applied"
    assert mock_graph.aupdate_state.called
    assert not mock_graph.ainvoke.called
