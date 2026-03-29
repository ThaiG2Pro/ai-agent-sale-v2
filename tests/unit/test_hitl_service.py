"""Unit tests for HITLService (T063)."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from services.hitl.schemas import ReviewActionCreate
from services.hitl.service import HITLService, _mark_incompatible


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


# Week 5: Checkpoint Durability Tests (T034-T035)


@pytest.mark.asyncio
async def test_mark_incompatible_updates_status(mock_db):
    """T034: _mark_incompatible() sets HITLMetadata.status = INCOMPATIBLE (FR-018)."""
    session_id = "session-123"
    error = KeyError("missing_field")

    # Mock the update result
    mock_result = MagicMock()
    mock_result.rowcount = 1
    mock_db.execute.return_value = mock_result

    await _mark_incompatible(session_id, error, mock_db)

    # Verify execute was called with UPDATE statement
    assert mock_db.execute.called
    # Verify commit was called (row was updated)
    assert mock_db.commit.called


@pytest.mark.asyncio
async def test_deserialization_error_marks_incompatible(mock_db, mock_graph):
    """T035: aget_state() KeyError → INCOMPATIBLE logged, HTTPException raised (FR-018)."""
    session_id = "session-123"

    # Mock aget_state to raise KeyError (schema mismatch)
    mock_graph.aget_state.side_effect = KeyError("missing_field_in_checkpoint")

    # Mock database execute for _mark_incompatible update
    mock_result = MagicMock()
    mock_result.rowcount = 1
    mock_db.execute.return_value = mock_result

    with pytest.raises(HTTPException) as exc_info:
        await HITLService.get_session_state(session_id, mock_graph, {}, mock_db)

    # Verify HTTPException was raised with 410 Gone status
    assert exc_info.value.status_code == 410
    # Verify _mark_incompatible was called (via db.execute)
    assert mock_db.execute.called
    assert mock_db.commit.called


@pytest.mark.asyncio
async def test_validation_error_marks_incompatible(mock_db, mock_graph):
    """T035: aget_state() ValidationError → INCOMPATIBLE marked (FR-018)."""
    session_id = "session-456"

    # Mock aget_state to raise ValidationError
    mock_graph.aget_state.side_effect = ValidationError.from_exception_data(
        "AgentState", [{"type": "value_error", "loc": ("field",), "msg": "Invalid state"}]
    )

    # Mock database execute
    mock_result = MagicMock()
    mock_result.rowcount = 1
    mock_db.execute.return_value = mock_result

    with pytest.raises(HTTPException) as exc_info:
        await HITLService.get_session_state(session_id, mock_graph, {}, mock_db)

    assert exc_info.value.status_code == 410
    assert mock_db.execute.called
