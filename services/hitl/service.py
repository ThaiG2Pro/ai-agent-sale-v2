"""
Why this exists: Service layer for HITL operations (Article I, III).
What it does: Orchestrates state retrieval, review processing, and message enqueueing.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from langgraph.errors import GraphInterrupt
from langgraph.types import Command
from pydantic import ValidationError
from sqlalchemy import select, update

from core.agent.graph import GRAPH_SCHEMA_VERSION
from models.schema import HITLMetadata, InterruptedSession, QueuedMessage, ReviewAction
from services.hitl.schemas import ApprovalPayload, ReviewActionCreate

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph
    from sqlalchemy.ext.asyncio import AsyncSession


logger = logging.getLogger(__name__)


# ─── request_edit: editable state fields with accepted value types ───
# Mirrors _VALID_STATE_KEYS in hitl_guard_node; unknown fields or wrong types
# are rejected with 422 instead of being silently written into the checkpoint.
EDITABLE_STATE_FIELDS: dict[str, tuple[type, ...]] = {
    "order_info": (dict,),
    "intent": (str,),
    "confidence_score": (int, float),
    "similarity_score": (int, float),
    "hitl_escalation_count": (int,),
    "response": (str,),
    "error": (str,),
}


def _validate_state_edits(state_edits: dict[str, Any]) -> None:
    """Real field/value validation for request_edit (T046 Part 1)."""
    issues: list[str] = []
    for field, value in state_edits.items():
        expected = EDITABLE_STATE_FIELDS.get(field)
        if expected is None:
            issues.append(
                f"unknown field '{field}' — editable fields: {sorted(EDITABLE_STATE_FIELDS)}"
            )
            continue
        # bool is an int subclass; never a valid edit value here
        if isinstance(value, bool) or not isinstance(value, expected):
            expected_names = "/".join(t.__name__ for t in expected)
            issues.append(f"field '{field}' expects {expected_names}, got {type(value).__name__}")
            continue
        if field in ("confidence_score", "similarity_score") and not 0.0 <= value <= 1.0:
            issues.append(f"field '{field}' must be within [0.0, 1.0], got {value}")
        elif field == "hitl_escalation_count" and value < 0:
            issues.append(f"field '{field}' must be >= 0, got {value}")
        elif field == "order_info":
            qty = value.get("quantity")
            if qty is not None and (isinstance(qty, bool) or not isinstance(qty, int) or qty < 1):
                issues.append("order_info.quantity must be a positive integer")
            for price_key in ("price", "approved_price"):
                price = value.get(price_key)
                if price is not None and (
                    isinstance(price, bool) or not isinstance(price, (int, float)) or price < 0
                ):
                    issues.append(f"order_info.{price_key} must be a non-negative number")
    if issues:
        raise HTTPException(
            status_code=422,
            detail={"error": "Invalid state_edits", "issues": issues},
        )


# ─── Week 5: Checkpoint Durability Helpers (FR-018) ───


async def _mark_incompatible(session_id: str, error: Exception, db: AsyncSession) -> None:
    """Mark checkpoint as INCOMPATIBLE due to schema mismatch (FR-018, T033).

    Logs error details and updates HITLMetadata.status to INCOMPATIBLE
    if a paused entry exists for this session.

    Args:
        session_id: Session ID with mismatched checkpoint
        error: Original deserialization/validation error
        db: Database session for metadata update
    """
    logger.error(
        "Checkpoint schema mismatch detected",
        extra={
            "session_id": session_id,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "graph_schema_version": GRAPH_SCHEMA_VERSION,
        },
    )

    # Update HITLMetadata status to INCOMPATIBLE if paused entry exists
    stmt = (
        update(HITLMetadata)
        .where(HITLMetadata.session_id == session_id)
        .values(status="INCOMPATIBLE")
    )
    result = await db.execute(stmt)
    if result.rowcount > 0:
        await db.commit()
        logger.info(
            "HITLMetadata updated to INCOMPATIBLE",
            extra={"session_id": session_id, "rows_updated": result.rowcount},
        )


class HITLService:
    @staticmethod
    async def get_session_state(
        session_id: str,
        graph: CompiledStateGraph,
        config: dict[str, Any],
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Retrieves paused session state and operational metadata (T042).

        Wraps aget_state() with error handling for checkpoint schema mismatches
        (FR-018, T032: KeyError, ValidationError, TypeError → INCOMPATIBLE marked).
        """
        # Try to retrieve checkpoint state; catch schema mismatch errors
        try:
            state = await graph.aget_state(config)
        except (KeyError, ValidationError, TypeError) as e:
            # Schema mismatch detected (e.g., from graph version change)
            await _mark_incompatible(session_id, e, db)
            raise HTTPException(
                status_code=410,  # Gone: checkpoint exists but is incompatible
                detail=f"Checkpoint schema incompatible (graph version {GRAPH_SCHEMA_VERSION})",
            ) from e

        # Query InterruptedSession for version and escalation_count
        stmt = select(InterruptedSession).where(InterruptedSession.session_id == session_id)
        result = await db.execute(stmt)
        interrupted = result.scalar_one_or_none()

        if not interrupted:
            raise HTTPException(status_code=404, detail="Session not found in InterruptedSession")

        # Query HITLMetadata for pause reason and status
        stmt_meta = (
            select(HITLMetadata)
            .where(HITLMetadata.session_id == session_id)
            .order_by(HITLMetadata.paused_at.desc())
            .limit(1)
        )
        result_meta = await db.execute(stmt_meta)
        hitl_meta = result_meta.scalar_one_or_none()

        # Query queued messages count
        stmt_q = select(QueuedMessage).where(
            QueuedMessage.session_id == session_id,
            QueuedMessage.processed == False,  # noqa: E712
        )
        result_q = await db.execute(stmt_q)
        queued_count = len(result_q.scalars().all())

        return {
            "session_id": session_id,
            "next_node": state.next[0] if state.next else None,
            "state_values": state.values,
            "hitl_metadata": {
                "pause_id": str(hitl_meta.pause_id) if hitl_meta else None,
                "paused_at": hitl_meta.paused_at.isoformat() if hitl_meta else None,
                "status": hitl_meta.status if hitl_meta else "unknown",
                "version": interrupted.version,
                "escalation_count": interrupted.escalation_count,
                "admin_id": hitl_meta.admin_id if hitl_meta else None,
            },
            "queued_messages_count": queued_count,
        }

    @staticmethod
    async def enqueue_message(session_id: str, message_text: str, db: AsyncSession) -> None:
        """Enqueues customer message received during pause (T043)."""
        new_msg = QueuedMessage(
            session_id=session_id,
            message_text=message_text,
            received_at=datetime.now(UTC),
            processed=False,
        )
        db.add(new_msg)
        await db.flush()
        await db.commit()

    @staticmethod
    async def check_idempotency(idempotency_key: str, db: AsyncSession) -> str | None:
        """Checks if a review action with this idempotency key already exists."""
        stmt = select(ReviewAction.action_id).where(
            ReviewAction.idempotency_key == idempotency_key
        )
        res = await db.execute(stmt)
        action_id = res.scalar_one_or_none()
        return str(action_id) if action_id else None

    @staticmethod
    async def _acknowledge_messages(
        session_id: str, message_ids: list[str], db: AsyncSession
    ) -> None:
        """Marks admin-acknowledged queued messages as processed (T046 Part 3).

        queue_consumer_node only drains processed == False, so acknowledged
        messages are skipped on resume.
        """
        if not message_ids:
            return
        try:
            parsed_ids = [uuid.UUID(mid) for mid in message_ids]
        except (ValueError, AttributeError, TypeError) as e:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid acknowledged_message_ids (must be UUIDs): {e}",
            ) from e
        await db.execute(
            update(QueuedMessage)
            .where(
                QueuedMessage.session_id == session_id,
                QueuedMessage.message_id.in_(parsed_ids),
            )
            .values(processed=True)
        )
        logger.info(
            "HITL acknowledged queued messages",
            extra={"session_id": session_id, "acknowledged_count": len(parsed_ids)},
        )

    @staticmethod
    async def _increment_version(session_id: str, expected_version: int, db: AsyncSession) -> None:
        """Increments version for optimistic locking (T044)."""
        stmt = (
            update(InterruptedSession)
            .where(
                InterruptedSession.session_id == session_id,
                InterruptedSession.version == expected_version,
            )
            .values(version=InterruptedSession.version + 1)
        )
        res = await db.execute(stmt)
        if res.rowcount == 0:
            curr = await db.get(InterruptedSession, session_id)
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "Version conflict — reload and retry",
                    "expected_version": expected_version,
                    "current_version": curr.version if curr else None,
                },
            )

    @staticmethod
    async def process_approve(
        payload: ReviewActionCreate,
        idempotency_key: str,
        db: AsyncSession,
        graph: CompiledStateGraph,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Processes admin approval with optimistic locking and Pattern B (T044)."""
        # 1. Check idempotency
        if action_id := await HITLService.check_idempotency(idempotency_key, db):
            return {"status": "hit", "action_id": action_id}

        # 2. Optimistic lock
        await HITLService._increment_version(payload.session_id, payload.expected_version, db)

        # 3. Insert ReviewAction
        new_action = ReviewAction(
            session_id=payload.session_id,
            pause_id=payload.pause_id,
            action=payload.action,
            state_edits=payload.state_edits,
            reason_or_comment=payload.reason_or_comment,
            admin_user_id=payload.admin_user_id,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key,
        )
        db.add(new_action)

        # 4. NOTE: Do NOT call aupdate_state here for "approve".
        # aupdate_state() creates a new checkpoint that CLEARS the pending
        # interrupt — subsequent ainvoke(Command(resume=...)) can't find the
        # interrupt to resume and exits immediately without running queue_consumer.
        # Instead, state_edits are forwarded inside ApprovalPayload and applied
        # by hitl_guard_node via Command(update={...}) after interrupt() returns.

        # 5. Mark admin-acknowledged queued messages before resume so
        # queue_consumer_node skips them (T046 Part 3).
        await HITLService._acknowledge_messages(
            payload.session_id, payload.acknowledged_message_ids, db
        )

        # 6. Set status="resuming"
        await db.execute(
            update(HITLMetadata)
            .where(HITLMetadata.pause_id == payload.pause_id)
            .values(status="resuming", admin_id=payload.admin_user_id)
        )
        await db.flush()

        # 7. Resume with exception handling
        try:
            resume_payload = ApprovalPayload(
                action=payload.action,
                admin_user_id=payload.admin_user_id,
                state_edits=payload.state_edits,
                approved_price=payload.approved_price,
                reason_or_comment=payload.reason_or_comment,
                acknowledged_message_ids=payload.acknowledged_message_ids,
            )
            result_state = await graph.ainvoke(
                Command(resume=resume_payload.model_dump()), config=config
            )

            # 8. Success: set old pause to "approved" (hitl_guard_node may have already done this
            # internally, but we ensure consistency here in case it didn't).
            await db.execute(
                update(HITLMetadata)
                .where(HITLMetadata.pause_id == payload.pause_id)
                .values(status="approved")
            )

            # 9. Detect if queue processing triggered a NEW HITL pause (e.g. customer
            # changed order during pause — MODIFY → new order → new approval needed).
            new_hitl: dict[str, Any] | None = None
            if isinstance(result_state, dict):
                interrupts = result_state.get("__interrupt__", [])
                if interrupts:
                    intr = interrupts[0]
                    iv = intr.value if hasattr(intr, "value") else {}
                    new_hitl = {
                        "pause_id": iv.get("pause_id") if isinstance(iv, dict) else None,
                        "reason": str(iv.get("reason", "")) if isinstance(iv, dict) else "",
                        "state_snapshot": (
                            iv.get("state_snapshot") if isinstance(iv, dict) else None
                        ),
                    }
                    logger.info(
                        f"New HITL pause created after queue processing for session "
                        f"{payload.session_id}: pause_id={new_hitl['pause_id']}"
                    )

            queue_response = (
                result_state.get("response") if isinstance(result_state, dict) else None
            )

        except GraphInterrupt as gi:
            # Defensive catch: GraphInterrupt escaped ainvoke — this means a new HITL pause
            # was triggered during queue processing (ainvoke didn't suppress it).
            # The old pause was already approved (hitl_guard_node sets it), so mark it done.
            await db.execute(
                update(HITLMetadata)
                .where(HITLMetadata.pause_id == payload.pause_id)
                .values(status="approved")
            )
            # Extract new pause info from the interrupt
            new_pause_id = None
            queue_response = None
            try:
                interrupts = gi.args[0] if gi.args else []
                if interrupts:
                    iv = getattr(interrupts[0], "value", {})
                    new_pause_id = iv.get("pause_id") if isinstance(iv, dict) else None
            except Exception:
                pass
            logger.info(
                f"GraphInterrupt caught in process_approve for session {payload.session_id}: "
                f"new_pause_id={new_pause_id}"
            )
            new_hitl = (
                {"pause_id": new_pause_id, "reason": "queue_modified"} if new_pause_id else None
            )
            return {
                "status": "resumed",
                "action_id": str(new_action.action_id),
                "new_hitl": new_hitl,
                "queue_response": queue_response,
            }
        except Exception as e:
            logger.error(f"Resume failed for session {payload.session_id}: {e}")
            await db.execute(
                update(HITLMetadata)
                .where(HITLMetadata.pause_id == payload.pause_id)
                .values(status="paused")
            )
            raise HTTPException(status_code=500, detail="Failed to resume graph") from e

        return {
            "status": "resumed",
            "action_id": str(new_action.action_id),
            "new_hitl": new_hitl,
            "queue_response": queue_response,
        }

    @staticmethod
    async def process_reject(
        payload: ReviewActionCreate,
        idempotency_key: str,
        db: AsyncSession,
        graph: CompiledStateGraph,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Processes admin rejection (T045)."""
        # 1. Idempotency
        if action_id := await HITLService.check_idempotency(idempotency_key, db):
            return {"status": "hit", "action_id": action_id}

        # 2. Lock
        await HITLService._increment_version(payload.session_id, payload.expected_version, db)

        # 3. Action
        new_action = ReviewAction(
            session_id=payload.session_id,
            pause_id=payload.pause_id,
            action=payload.action,
            reason_or_comment=payload.reason_or_comment,
            admin_user_id=payload.admin_user_id,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key,
        )
        db.add(new_action)

        # 4. Resume rejection path
        resume_payload = ApprovalPayload(
            action="reject",
            admin_user_id=payload.admin_user_id,
            reason_or_comment=payload.reason_or_comment,
        )
        try:
            await graph.ainvoke(Command(resume=resume_payload.model_dump()), config=config)
        except GraphInterrupt:
            # Defensive: GraphInterrupt shouldn't escape on reject path, but handle gracefully.
            logger.warning(f"GraphInterrupt on reject for session {payload.session_id} — ignoring")

        # 5. Update status
        await db.execute(
            update(HITLMetadata)
            .where(HITLMetadata.pause_id == payload.pause_id)
            .values(status="rejected", admin_id=payload.admin_user_id)
        )
        return {"status": "rejected", "action_id": str(new_action.action_id)}

    @staticmethod
    async def process_request_edit(
        payload: ReviewActionCreate,
        idempotency_key: str,
        db: AsyncSession,
        graph: CompiledStateGraph,
        config: dict[str, Any],
    ) -> dict[str, Any]:
        """Processes request_edit with synthetic message (T046)."""
        # 1. Idempotency
        if action_id := await HITLService.check_idempotency(idempotency_key, db):
            return {"status": "hit", "action_id": action_id}

        # 2. Lock
        await HITLService._increment_version(payload.session_id, payload.expected_version, db)

        # 3. Validation (T046 Part 1): real field/value checks against the
        # editable-state whitelist, not just a presence check.
        if not payload.state_edits:
            raise HTTPException(status_code=422, detail="state_edits required for request_edit")
        _validate_state_edits(payload.state_edits)

        # 4. Insert ReviewAction
        new_action = ReviewAction(
            session_id=payload.session_id,
            pause_id=payload.pause_id,
            action="request_edit",
            state_edits=payload.state_edits,
            reason_or_comment=payload.reason_or_comment,
            admin_user_id=payload.admin_user_id,
            expected_version=payload.expected_version,
            idempotency_key=idempotency_key,
        )
        db.add(new_action)

        # 5. Mark admin-acknowledged queued messages so queue_consumer skips them
        # (T046 Part 3).
        await HITLService._acknowledge_messages(
            payload.session_id, payload.acknowledged_message_ids, db
        )

        # 6. Pattern B: one synthetic admin_override record per edited field
        # (T046 Part 2-4). The messages channel uses the add_messages reducer,
        # so a deterministic id keyed by (field, pause_id) makes a repeated edit
        # of the same field REPLACE the earlier synthetic record in place
        # instead of inserting a duplicate on every request_edit call.
        timestamp = datetime.now(UTC).isoformat()
        synthetic_messages = [
            {
                "role": "system",
                "id": f"admin_override:{field}:{payload.pause_id}",
                "content": json.dumps(
                    {
                        "type": "admin_override",
                        "field": field,
                        "value": value,
                        "pause_id": str(payload.pause_id),
                        "admin_id": payload.admin_user_id,
                        "timestamp": timestamp,
                        "reason": payload.reason_or_comment or "",
                    },
                    ensure_ascii=False,
                    default=str,
                ),
            }
            for field, value in payload.state_edits.items()
        ]

        # Update state with edits AND history records
        await graph.aupdate_state(
            config,
            {**payload.state_edits, "messages": synthetic_messages},
            as_node="hitl_guard_node",
        )

        return {"status": "edit_applied", "action_id": str(new_action.action_id)}

    @staticmethod
    async def escalate_to_support(session_id: str, db: AsyncSession) -> None:
        """Escalates session to SupportQueue due to timeout (T049)."""
        from sqlalchemy import func
        from sqlalchemy.dialects.postgresql import insert

        from models.schema import SupportQueue

        now = datetime.now(UTC)

        # 1. Capture pause context BEFORE flipping the status, so the support
        # agent receives a real snapshot instead of an empty placeholder.
        meta_stmt = (
            select(HITLMetadata)
            .where(HITLMetadata.session_id == session_id, HITLMetadata.status == "paused")
            .order_by(HITLMetadata.paused_at.desc())
            .limit(1)
        )
        meta = (await db.execute(meta_stmt)).scalars().first()

        pending_stmt = (
            select(func.count())
            .select_from(QueuedMessage)
            .where(
                QueuedMessage.session_id == session_id,
                QueuedMessage.processed == False,  # noqa: E712
            )
        )
        pending_count = (await db.execute(pending_stmt)).scalar() or 0

        context_snapshot: dict[str, Any] = {
            "reason": "Automatic timeout after 60 minutes",
            "escalated_at": now.isoformat(),
            "pending_queued_messages": pending_count,
        }
        if meta is not None:
            context_snapshot.update(
                {
                    "pause_id": str(meta.pause_id),
                    "pause_reason": meta.pause_reason,
                    "paused_at": meta.paused_at.isoformat() if meta.paused_at else None,
                    "escalation_count": meta.escalation_count,
                    "timeout_notified_at": (
                        meta.timeout_notified_at.isoformat() if meta.timeout_notified_at else None
                    ),
                }
            )

        # 2. Update HITLMetadata status
        await db.execute(
            update(HITLMetadata)
            .where(HITLMetadata.session_id == session_id, HITLMetadata.status == "paused")
            .values(status="escalated", escalated_to_support_at=now)
        )

        # 3. Insert into SupportQueue
        stmt = (
            insert(SupportQueue)
            .values(
                session_id=session_id,
                reason="timeout_60min",
                created_at=now,
                status="pending",
                context_snapshot=context_snapshot,
            )
            .on_conflict_do_nothing(index_elements=["session_id"])
        )
        await db.execute(stmt)
        await db.flush()
