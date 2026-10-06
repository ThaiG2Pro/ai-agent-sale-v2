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

# WP-V2-4 risk helpers live in core.agent.hitl_risk (re-exported for callers/tests).
from core.agent.hitl_risk import (
    _history_factor,
    _order_value,
    _resolve_risk_tier,
    _risk_score,
    _tier1_eligibility,
    _value_norm,
)
from core.agent.state import AgentState, HITLReasonEnum
from core.config import settings
from models.schema import HITLMetadata, InterruptedSession
from services.hitl.schemas import ApprovalPayload

logger = logging.getLogger(__name__)

__all__ = [
    "_history_factor",
    "_order_value",
    "_resolve_risk_tier",
    "_risk_score",
    "_tier1_eligibility",
    "_value_norm",
    "hitl_guard_node",
]


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

    # Guard: ORDER_PLACEMENT variant mismatch check (e.g. requested 256GB when only 512GB exists)
    if intent == "ORDER_PLACEMENT" and state.get("variant_mismatch_msg"):
        return Command(
            goto="answer_node",
            update={"response": state["variant_mismatch_msg"]},
        )

    # Guard: ORDER_PLACEMENT needs order_info resolved by confidence_node
    # (product found in catalog). If order_info is None the product was not
    # found — surface a helpful response instead of pausing a human.
    if intent == "ORDER_PLACEMENT" and not state.get("order_info"):
        return Command(
            goto="answer_node",
            update={
                "response": (
                    "Xin lỗi, tôi không tìm thấy sản phẩm bạn đề cập "
                    "trong danh mục của chúng tôi. "
                    "Bạn có thể cho tôi biết tên chính xác hơn "
                    "hoặc xem danh sách sản phẩm hiện có không?"
                )
            },
        )

    # v3-0 P2 (T07): track which "20%" signals fired — feeds the structured
    # escalate reason in the handoff package.
    risk_signals: list[str] = list(state.get("risk_signals") or [])

    # v3-0 P2 (T06): NEGOTIATION with a product context pauses for the human
    # to decide the price — the draft stays at the ORIGINAL price with a
    # structured "khách xin giảm X" note; the agent never counter-offers.
    if settings.ORDER_HITL_V3_ENABLED and intent == "NEGOTIATION" and state.get("order_info"):
        trigger_hitl = True
        reason = HITLReasonEnum.PRICE_NEGOTIATION
        if "intent_negotiation" not in risk_signals:
            risk_signals.append("intent_negotiation")

    # Validation gate: Missing contact info (phone or address) must be requested from customer,
    # never passed to human review without fulfillment info (Closes Case 04).
    if intent == "ORDER_PLACEMENT" and state.get("order_info"):
        from core.agent.order_slots import missing_contact, park

        order_info = state["order_info"]
        missing_items = missing_contact(order_info)
        if missing_items:
            missing_str = " và ".join(missing_items)
            p_name = order_info.get("name") or order_info.get("product_name") or "sản phẩm"
            req_msg = (
                f"Dạ em đã ghi nhận bạn muốn đặt mua **{p_name}**. "
                f"Bạn vui lòng cung cấp thêm **{missing_str}** để shop hỗ trợ tạo đơn giao tận nơi cho bạn nhé! 😊"
            )
            # Park the draft cross-turn: the reply carrying phone/address
            # resumes it (router fast path) instead of losing the product.
            return Command(
                goto="answer_node",
                update={
                    "response": req_msg,
                    "hitl_triggered": False,
                    "pending_order": park(order_info, confidence_score),
                },
            )

    risk_tier: int | None = None
    if not trigger_hitl and settings.RISK_HITL_ENABLED:
        # WP-V2-4: composite risk score → 3 tiers (kill switch above).
        order_value = _order_value(state.get("order_info"))
        history = await _history_factor(state.get("customer_id"), db)
        risk = _risk_score(confidence_score, _value_norm(intent, order_value), history)
        risk_tier = _resolve_risk_tier(intent, order_value, risk)
        if intent == "ORDER_PLACEMENT" and risk_tier == 1:
            # v3-0 P2 (T07): Tier 1 auto-proceed is CONDITIONAL — unique
            # product, phone + address, stock. Any miss → Tier 2.
            eligible, why = await _tier1_eligibility(state.get("order_info"), db)
            if not eligible:
                risk_tier = 2
                logger.info("Tier1 conditions not met (%s) — order pauses at Tier 2", why)
        logger.info(
            "HITL risk assessment",
            extra={
                "session_id": session_id,
                "risk": round(risk, 4),
                "tier": risk_tier,
                "intent": intent,
                "order_value": order_value,
                "history_factor": history,
            },
        )
        if risk_tier == 3:
            # Tier 3: too risky for auto OR async approval — hand straight to
            # the human support queue.
            return Command(
                goto="customer_support_node",
                update={
                    "hitl_rejection_reason": "high_risk_tier3",
                    "risk_signals": [*risk_signals, "risk_score"],
                    "pending_order": None,
                },
            )
        if risk_tier == 2:
            trigger_hitl = True
            reason = (
                HITLReasonEnum.ORDER_APPROVAL
                if intent == "ORDER_PLACEMENT"
                else HITLReasonEnum.LOW_CONFIDENCE
            )
            if "risk_score" not in risk_signals:
                risk_signals.append("risk_score")
        # Tier 1: auto-proceed — no confidence/order trigger (cost guard below
        # still applies).
    elif not trigger_hitl:
        # Pre-V2-4 binary triggers (T025).
        if intent == "ORDER_PLACEMENT":
            trigger_hitl = True
            reason = HITLReasonEnum.ORDER_APPROVAL
        elif confidence_score < settings.AGENT_CONFIDENCE_THRESHOLD:
            trigger_hitl = True
            reason = HITLReasonEnum.LOW_CONFIDENCE

    # Cost Check (T026, T072)
    if not trigger_hitl:
        from services.hitl.cost_guard import (
            estimate_tokens,
            get_compressed_context_text,
        )

        messages = state.get("messages", [])
        compressed_text = get_compressed_context_text(
            messages, intent=intent, order_info=state.get("order_info")
        )
        estimated_tokens = estimate_tokens(compressed_text)

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

        pause_id, order_info = await _persist_pause(
            db,
            state,
            session_id=session_id,
            reason=reason,
            escalation_count=escalation_count,
            risk_signals=risk_signals,
        )
        holding_msg = _holding_message(order_info)

        # Call interrupt() (FR-001)
        # Execution pauses here. LangGraph checkpoints state and suspends.
        # ainvoke() returns the state snapshot at this point.
        # The resume value (admin payload) is returned when graph is resumed.
        interrupt_result = interrupt(
            {
                "pause_id": str(pause_id),
                "reason": reason,
                "session_id": session_id,
                "response": holding_msg,
                "state_snapshot": {
                    "intent": intent,
                    "order_info": order_info,
                    "confidence_score": confidence_score,
                    "response": holding_msg,
                },
            }
        )

        # --- CODE RESUMES HERE ---
        return await _handle_resume(
            db,
            interrupt_result,
            session_id=session_id,
            pause_id=pause_id,
            order_info=order_info,
            escalation_count=escalation_count,
        )

    # 6. Default: proceed (store token estimate for observability)
    update: dict = (
        {"estimated_token_cost": estimated_tokens} if estimated_tokens is not None else {}
    )
    if risk_signals:
        update["risk_signals"] = risk_signals

    # WP-V2-4 Tier 1 auto-approval: a low-risk order (small value, known
    # customer, high confidence) skips the human pause and flows down the same
    # execution path an approval would take (queue_consumer → freshness →
    # order_execution). Only reachable with RISK_HITL_ENABLED and never for
    # high-value or unknown-value orders (safety invariant in _resolve_risk_tier).
    if intent == "ORDER_PLACEMENT" and risk_tier == 1:
        logger.info(f"HITL Tier1 auto-approval for session {session_id}")
        return Command(
            goto="queue_consumer_node",
            update={
                **update,
                "hitl_approved": True,
                "hitl_triggered": False,
                "order_info": state.get("order_info"),
                "pending_order": None,
            },
        )

    return Command(goto="answer_node", update=update)


async def _persist_pause(
    db: AsyncSession,
    state: AgentState,
    *,
    session_id: str,
    reason,
    escalation_count: int,
    risk_signals: list[str],
):
    """Record the pause (HITLMetadata + InterruptedSession + draft order + handoff).

    Returns (pause_id, order_info). On resume (LangGraph re-runs the node) the
    existing pause is reused and no rows are written.
    """
    order_info = state.get("order_info")
    # Record pause in DB (T005, T009)
    # LangGraph re-runs this node from the start on resume (checkpoint is input state),
    # so we must detect resume vs. fresh trigger via DB to avoid duplicate records.
    # On resume, service.py sets status="resuming" before calling graph.ainvoke().
    #
    # WHY DB status and not a state flag (V3-5): service.py cannot write the
    # flag into checkpoint state — aupdate_state() before resume creates a new
    # checkpoint that CLEARS the pending interrupt (see the NOTE in
    # HITLService.review_action), and the resume payload only becomes visible
    # AFTER interrupt() returns, i.e. below this dedup check. The DB lookup is
    # the only signal available at this point. Known fragility: a second fresh
    # turn racing the "resuming" window would match this query and reuse the
    # pause_id instead of creating its own record — accepted, since sessions
    # are single-conversation and a paused session queues new messages instead
    # of re-entering the graph. Behavior locked by
    # tests/unit/test_hitl_guard_node.py::test_hitl_guard_resume_*.
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
        # Resume mode: reuse existing pause_id, skip DB inserts.
        pause_id = existing_record.pause_id
        # v3-0 P2 (T05): the draft row was created on the fresh trigger,
        # but its id lives outside checkpointed state (interrupt() fired
        # before any Command update) — reattach it so order_execution
        # confirms the draft row instead of inserting a parallel record.
        if settings.ORDER_HITL_V3_ENABLED and order_info and not order_info.get("draft_order_id"):
            try:
                from models.schema import Order as _Order

                latest_draft_id = (
                    await db.execute(
                        sa_select(_Order.id)
                        .where(
                            _Order.session_id == session_id,
                            _Order.status == "pending_review",
                        )
                        .order_by(_Order.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if latest_draft_id is not None:
                    order_info = {**order_info, "draft_order_id": str(latest_draft_id)}
            except Exception:
                logger.warning("draft id reattach failed on resume", exc_info=True)
    else:
        # Fresh trigger: create new records
        pause_id = uuid7()

        # v3-0 P2 (T05): materialize the draft as an orders row (status
        # pending_review). A re-pause on a changed order creates a NEW
        # draft superseding the previous one — the agent never edits.
        if settings.ORDER_HITL_V3_ENABLED and order_info and order_info.get("product_id"):
            try:
                from services.draft_orders import create_draft

                draft = await create_draft(
                    db,
                    session_id=session_id,
                    customer_id=state.get("customer_id") or "anonymous",
                    order_info=order_info,
                    supersedes_id=order_info.get("draft_order_id"),
                )
                order_info = dict(draft.order_info)
            except Exception:
                logger.warning("draft creation failed; pausing without draft row", exc_info=True)

        new_metadata = HITLMetadata(
            pause_id=pause_id,
            session_id=session_id,
            pause_reason=reason,
            status="paused",
            escalation_count=escalation_count,
            paused_at=datetime.now(UTC),
        )

        # v3-0 P2 (T07/T13): build + persist the 4-part handoff package
        # and notify the Telegram admin chat. Best-effort — a package or
        # notify failure never blocks the pause.
        handoff_package = None
        if settings.ORDER_HITL_V3_ENABLED:
            try:
                from services.hitl.handoff import build_handoff_package

                pkg_state = {
                    **state,
                    "order_info": order_info,
                    "risk_signals": risk_signals,
                }
                handoff_package = await build_handoff_package(
                    db, pkg_state, pause_reason=str(reason)
                )
                new_metadata.handoff_package = handoff_package
            except Exception:
                logger.warning("handoff package build failed", exc_info=True)

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

        # v3-0 P2 (T13): one HTML message to the admin chat — 3 sections
        # inline + intent log behind a callback + review buttons.
        if handoff_package is not None:
            try:
                from services.hitl.admin_notify import notify_admin_handoff

                await notify_admin_handoff(handoff_package, str(pause_id), session_id)
            except Exception:
                logger.warning("admin handoff notify failed", exc_info=True)
    return pause_id, order_info


def _holding_message(order_info: dict | None) -> str:
    """Customer text while the order waits for review.

    The old variant claimed "hiện còn hàng sẵn" whenever the message matched a
    stock regex — without checking stock. Name the product, claim nothing.
    """
    p_name = (order_info or {}).get("name") or (order_info or {}).get("product_name")
    product = f" **{p_name}**" if p_name else ""
    return (
        f"Yêu cầu đặt hàng{product} của bạn đang chờ xác nhận từ nhân viên. "
        "Chúng tôi sẽ phản hồi sớm nhất có thể. Cảm ơn bạn đã kiên nhẫn!"
    )


async def _handle_resume(
    db: AsyncSession,
    interrupt_result,
    *,
    session_id: str,
    pause_id,
    order_info: dict | None,
    escalation_count: int,
) -> Command:
    """Admin decision after interrupt(): approve / reject / request_edit (T027, T028)."""
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

            # SC3-fix: merge admin approved_price override into existing order_info.
            # This allows admin to grant discounts at approval time without replacing
            # the full order_info (which would lose product_id, sku, quantity, etc.).
            final_order_info = order_info or {}
            if payload.approved_price is not None and final_order_info:
                final_order_info = {
                    **final_order_info,
                    "approved_price": payload.approved_price,
                }
                logger.info(
                    "SC3: admin approved_price override applied: %.0f → %.0f",
                    (order_info or {}).get("approved_price", 0),
                    payload.approved_price,
                )

            # T027: Success path — include order_info so freshness validator can proceed
            return Command(
                goto="queue_consumer_node",
                update={
                    "hitl_approved": True,
                    "hitl_triggered": False,
                    "hitl_pause_id": str(pause_id),
                    "order_info": final_order_info,
                    # v3-0 P2 (O27): the admin's note travels to the
                    # customer with the order confirmation.
                    "hitl_admin_reason": payload.reason_or_comment,
                    "pending_order": None,
                    **safe_edits,
                },
            )
        elif payload.action == "reject":
            # T028: Increment escalation count and route to support
            new_count = escalation_count + 1

            # v3-0 P2 (T05): a rejected draft leaves the active set.
            draft_id = (order_info or {}).get("draft_order_id")
            if settings.ORDER_HITL_V3_ENABLED and draft_id:
                try:
                    import uuid as _uuid

                    from sqlalchemy import update as sa_update

                    from models.schema import Order as _Order

                    await db.execute(
                        sa_update(_Order)
                        .where(_Order.id == _uuid.UUID(str(draft_id)))
                        .values(status="cancelled")
                    )
                    await db.commit()
                except Exception:
                    logger.warning("draft cancel on reject failed", exc_info=True)

            return Command(
                goto="customer_support_node",
                update={
                    "hitl_rejection_reason": payload.reason_or_comment,
                    "hitl_escalation_count": new_count,
                    "hitl_triggered": False,
                    "hitl_pause_id": str(pause_id),
                    "pending_order": None,
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
    return Command(goto="answer_node", update={"error": "Invalid HITL resume payload"})
