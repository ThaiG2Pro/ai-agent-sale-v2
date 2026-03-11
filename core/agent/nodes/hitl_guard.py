"""hitl_guard_node — confidence + cost guard; calls interrupt() on threshold breach."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from langgraph.types import Command, interrupt
from sqlalchemy.dialects.postgresql import insert
from uuid_utils import uuid7

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from sqlalchemy.ext.asyncio import AsyncSession

from core.agent.state import AgentState, HITLReasonEnum
from core.config import settings
from models.schema import HITLMetadata, InterruptedSession
from services.hitl.schemas import ApprovalPayload

logger = logging.getLogger(__name__)


async def hitl_guard_node(state: AgentState, config: RunnableConfig) -> Command:
    """Confidence + cost guard; fires interrupt() if thresholds breached (T025, T026).

    Flow:
    1. Check if already approved (skip guard).
    2. Check confidence threshold (FR-005).
    3. Check token cost threshold (FR-006).
    4. If breach:
       - Check escalation limit (FR-015).
       - Persist pause metadata (T005, T009).
       - Call interrupt().
    5. On resume:
       - Handle approve/reject/request_edit (T027, T028).
    """
    # 1. Get DB session from config
    db = cast("AsyncSession", config["configurable"].get("db"))
    session_id = state["session_id"]

    # 2. Check if already approved or triggered in this turn
    if state.get("hitl_approved"):
        return Command(goto="answer_node")

    # 3. Determine if we need to trigger HITL
    trigger_hitl = False
    reason = None

    intent = state.get("intent")
    confidence_score = state.get("confidence_score", 0.0)

    # Confidence Check (T025)
    # Trigger if explicit ORDER_PLACEMENT or low confidence for other intents
    if intent == "ORDER_PLACEMENT":
        trigger_hitl = True
        reason = HITLReasonEnum.ORDER_APPROVAL
    elif confidence_score < settings.AGENT_CONFIDENCE_THRESHOLD:
        trigger_hitl = True
        reason = HITLReasonEnum.LOW_CONFIDENCE

    # Cost Check (T026, T072)
    if not trigger_hitl:
        from services.hitl.cost_guard import (
            estimate_tokens_heuristic,
            get_compressed_context_text,
        )

        messages = state.get("messages", [])
        compressed_text = get_compressed_context_text(
            messages, intent=intent, order_info=state.get("order_info")
        )
        estimated_tokens = estimate_tokens_heuristic(compressed_text)

        if estimated_tokens > settings.HITL_COST_THRESHOLD_TOKENS:
            trigger_hitl = True
            reason = HITLReasonEnum.COST_LIMIT
    else:
        estimated_tokens = None

    # 4. Handle HITL Trigger
    if trigger_hitl:
        # Check escalation limit (T025, FR-015)
        escalation_count = state.get("hitl_escalation_count", 0)
        if escalation_count >= settings.HITL_MAX_ESCALATION_COUNT:
            logger.info(f"Max HITL escalation reached for session {session_id}")
            return Command(
                goto="customer_support_node",
                update={"hitl_rejection_reason": "max_escalation_reached"},
            )

        # Record pause in DB (T005, T009)
        # LangGraph re-runs this node from the start on resume (checkpoint is input state),
        # so we must detect resume vs. fresh trigger via DB to avoid duplicate records.
        # On resume, service.py sets status="resuming" before calling graph.ainvoke().
        from sqlalchemy import select as sa_select

        existing_stmt = (
            sa_select(HITLMetadata)
            .where(HITLMetadata.session_id == session_id)
            .where(HITLMetadata.status.in_(["paused", "resuming"]))
            .order_by(HITLMetadata.paused_at.desc())
            .limit(1)
        )
        existing_result = await db.execute(existing_stmt)
        existing_record = existing_result.scalar_one_or_none()

        if existing_record:
            # Resume mode: reuse existing pause_id, skip DB inserts
            pause_id = existing_record.pause_id
        else:
            # Fresh trigger: create new records
            pause_id = uuid7()

            new_metadata = HITLMetadata(
                pause_id=pause_id,
                session_id=session_id,
                pause_reason=reason,
                status="paused",
                escalation_count=escalation_count,
                paused_at=datetime.now(UTC),
            )
            db.add(new_metadata)

            # Upsert InterruptedSession
            stmt = (
                insert(InterruptedSession)
                .values(
                    session_id=session_id,
                    next_node="hitl_guard_node",
                    reason=reason,
                    escalation_count=escalation_count,
                    version=0,
                    timestamp=datetime.now(UTC),
                )
                .on_conflict_do_update(
                    index_elements=["session_id"],
                    set_={
                        "next_node": "hitl_guard_node",
                        "reason": reason,
                        "timestamp": datetime.now(UTC),
                        "escalation_count": escalation_count,
                    },
                )
            )
            await db.execute(stmt)
            await db.flush()
            await db.commit()

        # For ORDER_PLACEMENT: order_info should already be in state (set by
        # confidence_node before this node ran). Use it if present.
        order_info = state.get("order_info")

        # Call interrupt() (FR-001)
        # Execution pauses here. LangGraph checkpoints state and suspends.
        # ainvoke() returns the state snapshot at this point.
        # The resume value (admin payload) is returned when graph is resumed.
        interrupt_result = interrupt(
            {
                "pause_id": str(pause_id),
                "reason": reason,
                "session_id": session_id,
                "state_snapshot": {
                    "intent": intent,
                    "order_info": order_info,
                    "confidence_score": confidence_score,
                },
            }
        )

        # --- CODE RESUMES HERE ---

        # 5. Handle Resume (T027, T028)
        try:
            payload = ApprovalPayload.model_validate(interrupt_result)

            if payload.action == "approve":
                # Mark this pause as approved immediately so downstream re-pauses
                # (e.g. MODIFY_ORDER from queue_consumer) see a clean slate in the DB.
                from sqlalchemy import update as sa_update

                await db.execute(
                    sa_update(HITLMetadata)
                    .where(HITLMetadata.pause_id == pause_id)
                    .values(status="approved", admin_id=payload.admin_user_id)
                )
                await db.commit()

                # Apply admin state_edits (e.g. approved_price override) if provided.
                # We filter to known AgentState keys to discard Swagger example artifacts
                # like {"additionalProp1": {}} that would otherwise corrupt downstream state.
                _VALID_STATE_KEYS = {
                    "order_info",
                    "intent",
                    "confidence_score",
                    "similarity_score",
                    "hitl_escalation_count",
                    "response",
                    "error",
                }
                safe_edits: dict = {}
                if payload.state_edits:
                    safe_edits = {
                        k: v for k, v in payload.state_edits.items() if k in _VALID_STATE_KEYS
                    }

                # T027: Success path — include order_info so freshness validator can proceed
                return Command(
                    goto="queue_consumer_node",
                    update={
                        "hitl_approved": True,
                        "hitl_triggered": False,
                        "hitl_pause_id": str(pause_id),
                        "order_info": order_info,
                        **safe_edits,
                    },
                )
            elif payload.action == "reject":
                # T028: Increment escalation count and route to support
                new_count = escalation_count + 1
                return Command(
                    goto="customer_support_node",
                    update={
                        "hitl_rejection_reason": payload.reason_or_comment,
                        "hitl_escalation_count": new_count,
                        "hitl_triggered": False,
                        "hitl_pause_id": str(pause_id),
                    },
                )
            elif payload.action == "request_edit":
                # Pattern B: Admin applied edits and wants to re-review or unpause.
                # If they applied edits via update_state and then called resume,
                # we just go to queue_consumer_node to process any pending messages.
                return Command(
                    goto="queue_consumer_node",
                    update={
                        "hitl_triggered": False,
                        "hitl_pause_id": str(pause_id),
                    },
                )
        except Exception as e:
            logger.error(f"Failed to process interrupt result for session {session_id}: {e}")
            return Command(goto="answer_node", update={"error": "Invalid HITL resume payload"})

    # 6. Default: proceed to answer_node (store token estimate for observability)
    update: dict = (
        {"estimated_token_cost": estimated_tokens} if estimated_tokens is not None else {}
    )
    return Command(goto="answer_node", update=update)
