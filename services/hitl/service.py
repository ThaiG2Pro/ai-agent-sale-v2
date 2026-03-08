"""
Why this exists: Service layer for HITL operations (Article I, III).
What it does: Orchestrates state retrieval, review processing, and message enqueueing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from langgraph.types import Command
from sqlalchemy import select, update

from models.schema import HITLMetadata, InterruptedSession, QueuedMessage, ReviewAction
from services.hitl.schemas import ApprovalPayload, ReviewActionCreate

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph
    from sqlalchemy.ext.asyncio import AsyncSession


logger = logging.getLogger(__name__)


class HITLService:
    @staticmethod
    async def get_session_state(
        session_id: str,
        graph: CompiledStateGraph,
        config: dict[str, Any],
        db: AsyncSession,
    ) -> dict[str, Any]:
        """Retrieves paused session state and operational metadata (T042)."""
        state = await graph.aget_state(config)

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

    @staticmethod
    async def _check_idempotency(idempotency_key: str, db: AsyncSession) -> str | None:
        """Checks if a review action with this idempotency key already exists."""
        stmt = select(ReviewAction.action_id).where(
            ReviewAction.idempotency_key == idempotency_key
        )
        res = await db.execute(stmt)
        action_id = res.scalar_one_or_none()
        return str(action_id) if action_id else None

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
        if action_id := await HITLService._check_idempotency(idempotency_key, db):
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

        # 4. State updates (Pattern B)
        if payload.state_edits:
            await graph.aupdate_state(config, payload.state_edits, as_node="hitl_review_node")

        # 5. Set status="resuming"
        await db.execute(
            update(HITLMetadata)
            .where(HITLMetadata.pause_id == payload.pause_id)
            .values(status="resuming", admin_id=payload.admin_user_id)
        )
        await db.flush()

        # 6. Resume with exception handling
        try:
            resume_payload = ApprovalPayload(
                action=payload.action,
                admin_user_id=payload.admin_user_id,
                state_edits=payload.state_edits,
                reason_or_comment=payload.reason_or_comment,
            )
            await graph.ainvoke(Command(resume=resume_payload.model_dump()), config=config)

            # 7. Success: set status="approved"
            await db.execute(
                update(HITLMetadata)
                .where(HITLMetadata.pause_id == payload.pause_id)
                .values(status="approved")
            )
        except Exception as e:
            logger.error(f"Resume failed for session {payload.session_id}: {e}")
            await db.execute(
                update(HITLMetadata)
                .where(HITLMetadata.pause_id == payload.pause_id)
                .values(status="paused")
            )
            raise HTTPException(status_code=500, detail="Failed to resume graph") from e

        return {"status": "resumed", "action_id": str(new_action.action_id)}

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
        if action_id := await HITLService._check_idempotency(idempotency_key, db):
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
        await graph.ainvoke(Command(resume=resume_payload.model_dump()), config=config)

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
        if action_id := await HITLService._check_idempotency(idempotency_key, db):
            return {"status": "hit", "action_id": action_id}

        # 2. Lock
        await HITLService._increment_version(payload.session_id, payload.expected_version, db)

        # 3. Validation (T046 Part 1)
        # Generic validation against downstream schema is complex
        # For now, we perform a basic presence check.
        if not payload.state_edits:
            raise HTTPException(status_code=422, detail="state_edits required for request_edit")

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

        # 5. Pattern B: Structured synthetic message (T046 Part 2-4)
        state = await graph.aget_state(config)
        messages = list(state.values.get("messages", []))

        # Find last human message to insert AFTER it
        insertion_idx = len(messages)
        for i in range(len(messages) - 1, -1, -1):
            if hasattr(messages[i], "type") and messages[i].type == "human":
                insertion_idx = i + 1
                break
            # Fallback for dict-based messages if any
            if isinstance(messages[i], dict) and messages[i].get("role") == "user":
                insertion_idx = i + 1
                break

        # Build structured synthetic record
        synthetic = {
            "type": "admin_override",
            "pause_id": str(payload.pause_id),
            "admin_id": payload.admin_user_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "reason": payload.reason_or_comment or "",
            "edits": payload.state_edits,
        }

        # Update state with edits AND history record
        await graph.aupdate_state(
            config,
            {
                **(payload.state_edits or {}),
                "messages": [*messages[:insertion_idx], synthetic, *messages[insertion_idx:]],
            },
            as_node="hitl_review_node",
        )

        return {"status": "edit_applied", "action_id": str(new_action.action_id)}
