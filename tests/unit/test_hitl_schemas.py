"""Unit tests for HITL Pydantic schemas (T064)."""

import pytest
from pydantic import ValidationError

from services.hitl.schemas import (
    ApprovalPayload,
    QueuedMessageBatch,
    QueueIntentResult,
    ReviewActionCreate,
)


def test_review_action_create_validation():
    """Test validation logic for request_edit and reject (T064)."""
    base = {"session_id": "s1", "pause_id": "p1", "expected_version": 0, "admin_user_id": "a1"}

    # Happy path approve
    ReviewActionCreate(action="approve", **base)

    # request_edit requires state_edits
    with pytest.raises(ValidationError) as exc:
        ReviewActionCreate(action="request_edit", **base)
    assert "state_edits is required" in str(exc.value)

    # reject requires reason
    with pytest.raises(ValidationError) as exc:
        ReviewActionCreate(action="reject", **base)
    assert "reason_or_comment is required" in str(exc.value)


def test_approval_payload_round_trip():
    """Test serialization round-trip (T064)."""
    payload = ApprovalPayload(action="approve", admin_user_id="admin1", state_edits={"price": 500})

    dumped = payload.model_dump()
    validated = ApprovalPayload.model_validate(dumped)

    assert validated.action == "approve"
    assert validated.state_edits == {"price": 500}


def test_queued_message_batch_computed_fields():
    """Test automatic flag computation (T064)."""
    msg1 = QueueIntentResult(message_id="m1", text="stop", intent="CANCEL", confidence=0.9)
    msg2 = QueueIntentResult(message_id="m2", text="change", intent="MODIFY_ORDER", confidence=0.8)

    batch = QueuedMessageBatch(session_id="s1", messages=[msg1, msg2])

    assert batch.has_cancel is True
    assert batch.has_modify is True
    assert batch.has_confirm is False
