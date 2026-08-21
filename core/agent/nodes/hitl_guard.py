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

# ── WP-V2-4 risk-score HITL tiers (anti approval-fatigue) ──────────────────
# risk = W_CONF·(1-confidence) + W_VALUE·order_value_norm + W_HISTORY·history
# Tier 1: auto-proceed. Tier 2: interrupt (pre-V2-4 behavior). Tier 3: straight
# to the support queue. Kill switch RISK_HITL_ENABLED=False restores the old
# binary triggers (ORDER_PLACEMENT OR confidence < threshold).


def _order_value(order_info: dict | None) -> float | None:
    """Total order value in VND, or None when it cannot be determined."""
    if not order_info:
        return None
    price = order_info.get("price")
    if price is None:
        return None
    try:
        quantity = float(order_info.get("quantity") or 1)
        value = float(price) * quantity
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


async def _history_factor(customer_id: str | None, db: AsyncSession | None) -> float:
    """Customer-history risk term in [0,1] from intent_tracking. 1.0 = unknown.

    Conservative defaults: no customer_id, no rows, or a DB error all read as
    a NEW customer (max history risk).
    """
    if not customer_id or db is None:
        return 1.0
    try:
        from sqlalchemy import select as sa_select

        from models.schema import IntentStatus, IntentTracking

        result = await db.execute(
            sa_select(IntentTracking.status).where(IntentTracking.customer_id == customer_id)
        )
        statuses = {str(s) for s in result.scalars().all()}
    except Exception:
        logger.warning("history_factor lookup failed; treating customer as new", exc_info=True)
        return 1.0
    if not statuses:
        return 1.0
    if IntentStatus.CONVERTED in statuses:
        return 0.0  # Has purchased before — lowest history risk
    if statuses & {IntentStatus.ENGAGED, IntentStatus.AWAITING_QUOTE, IntentStatus.CONTACTED}:
        return 0.5  # Known, actively engaged customer
    return 0.8  # Tracked but NEW/LOST only


def _value_norm(intent: str | None, order_value: float | None) -> float:
    """Order-value risk term in [0,1].

    Only ORDER_PLACEMENT has money at stake: a missing/unparseable value there
    reads as MAX risk (conservative); other intents carry no value risk.
    """
    if intent != "ORDER_PLACEMENT":
        return 0.0
    if order_value is None:
        return 1.0  # Conservative: unknown order value is high risk
    return min(order_value / settings.HITL_ORDER_VALUE_NORM_CAP, 1.0)


def _risk_score(confidence: float, value_norm: float, history: float) -> float:
    """Weighted composite risk in [0,1]."""
    conf = min(max(confidence, 0.0), 1.0)
    return (
        settings.HITL_RISK_W_CONF * (1.0 - conf)
        + settings.HITL_RISK_W_VALUE * value_norm
        + settings.HITL_RISK_W_HISTORY * history
    )


def _resolve_risk_tier(intent: str | None, order_value: float | None, risk: float) -> int:
    """Map risk score to tier 1/2/3 and enforce the safety invariant.

    SAFETY INVARIANT (non-configurable): an ORDER_PLACEMENT whose value is
    unknown — or at/above HITL_TIER1_MAX_ORDER_VALUE (T07 default 10tr) — is
    always >= Tier 2. No weight/threshold tuning can auto-approve such an
    order. v3-0 P2 (T07): the pre-P2 hardcode "every ORDER pauses at Tier 2"
    only remains under the ORDER_HITL_V3_ENABLED=False kill switch; further
    Tier-1 conditions (unique product, phone+address, stock) are checked by
    _tier1_eligibility in the node.
    """
    if intent == "ORDER_PLACEMENT" and not settings.ORDER_HITL_V3_ENABLED:
        # Pre-v3-0-P2 behavior: all orders pause at Tier 2 for review.
        return 2

    if risk >= settings.HITL_RISK_TIER3_THRESHOLD:
        tier = 3
    elif risk >= settings.HITL_RISK_TIER1_THRESHOLD:
        tier = 2
    else:
        tier = 1

    if intent == "ORDER_PLACEMENT" and (
        order_value is None or order_value >= settings.HITL_TIER1_MAX_ORDER_VALUE
    ):
        tier = max(tier, 2)
    return tier


async def _tier1_eligibility(order_info: dict | None, db: AsyncSession | None) -> tuple[bool, str]:
    """v3-0 P2 (T07): remaining Tier-1 auto-proceed conditions.

    Value < threshold is already enforced by _resolve_risk_tier; here:
    uniquely determined product, phone + address present, stock sufficient.
    Any missing/unverifiable condition falls back to Tier 2 — never Tier 1.
    """
    if not order_info or not order_info.get("product_id"):
        return False, "no_unique_product"
    if not order_info.get("phone"):
        return False, "missing_phone"
    if not order_info.get("address"):
        return False, "missing_address"
    if db is None:
        return False, "no_db_for_stock_check"
    try:
        from sqlalchemy import select as sa_select

        from models.schema import Product

        qty = int(order_info.get("quantity") or 1)
        stock = (
            await db.execute(
                sa_select(Product.stock_quantity).where(Product.id == order_info["product_id"])
            )
        ).scalar_one_or_none()
        if stock is None or stock < qty:
            return False, "insufficient_stock"
    except Exception:
        logger.warning("Tier1 stock check failed; falling back to Tier 2", exc_info=True)
        return False, "stock_check_error"
    return True, "ok"


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
        order_info = state["order_info"]
        has_phone = bool(order_info.get("phone"))
        has_address = bool(order_info.get("address"))
        if not has_phone or not has_address:
            missing_items = []
            if not has_phone:
                missing_items.append("số điện thoại")
            if not has_address:
                missing_items.append("địa chỉ nhận hàng")
            missing_str = " và ".join(missing_items)
            p_name = order_info.get("name") or order_info.get("product_name") or "sản phẩm"
            req_msg = (
                f"Dạ em đã ghi nhận bạn muốn đặt mua **{p_name}**. "
                f"Bạn vui lòng cung cấp thêm **{missing_str}** để shop hỗ trợ tạo đơn giao tận nơi cho bạn nhé! 😊"
            )
            return Command(
                goto="answer_node",
                update={
                    "response": req_msg,
                    "hitl_paused": False,
                    "hitl_triggered": False,
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

        order_info = state.get("order_info")

        if existing_record:
            # Resume mode: reuse existing pause_id, skip DB inserts.
            pause_id = existing_record.pause_id
            # v3-0 P2 (T05): the draft row was created on the fresh trigger,
            # but its id lives outside checkpointed state (interrupt() fired
            # before any Command update) — reattach it so order_execution
            # confirms the draft row instead of inserting a parallel record.
            if (
                settings.ORDER_HITL_V3_ENABLED
                and order_info
                and not order_info.get("draft_order_id")
            ):
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
                    logger.warning(
                        "draft creation failed; pausing without draft row", exc_info=True
                    )

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

        user_msg = ""
        for m in reversed(state.get("messages", [])):
            if getattr(m, "type", None) == "human" or getattr(m, "role", None) == "user":
                user_msg = getattr(m, "content", "")
                break

        import re as _re

        is_avail_query = bool(
            _re.search(r"còn\s*(?:hàng|không|k\b|ko\b)|sẵn\s*hàng", user_msg, _re.IGNORECASE)
        )
        if is_avail_query and order_info:
            p_name = order_info.get("name") or order_info.get("product_name") or "sản phẩm"
            holding_msg = (
                f"Dạ sản phẩm **{p_name}** hiện còn hàng sẵn tại shop ạ! "
                f"Yêu cầu đặt hàng của bạn đang chờ xác nhận từ nhân viên, shop sẽ phản hồi sớm nhất có thể. Cảm ơn bạn đã kiên nhẫn! 🙏"
            )
        else:
            holding_msg = (
                "Yêu cầu đặt hàng của bạn đang chờ xác nhận từ nhân viên. "
                "Chúng tôi sẽ phản hồi sớm nhất có thể. Cảm ơn bạn đã kiên nhẫn!"
            )

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
            },
        )

    return Command(goto="answer_node", update=update)
