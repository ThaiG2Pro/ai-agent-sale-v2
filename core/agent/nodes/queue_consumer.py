"""queue_consumer_node — processes queued messages after unpause.

Why: Central integration point after HITL resume. Processes customer messages
received while paused, ensures history consistency by closing orphan tool calls,
and routes to the next step (execute, cancel, or re-pause).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command
from sqlalchemy import select, update

from core.agent.queue_intents import (
    _ADD_ON_PATTERNS,
    _INFO_QUERY_PATTERNS,
    _extract_proposed_price,
    _extract_quantity,
    _keyword_classify_batch,
    _postvalidate_llm_batch,
)
from core.config import settings
from models.schema import QueuedMessage
from services.ai import AIGateway
from services.hitl.schemas import QueuedMessageBatch

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.agent.state import AgentState

logger = logging.getLogger(__name__)


async def _resolve_new_product_from_modify(
    queued_rows: list,
    db: AsyncSession,
    state: AgentState,
) -> dict | None:
    """SC08 fix: extract and resolve the new product name from MODIFY messages.

    Uses RAG retrieval on the queued message text to find the best matching product.
    Falls back to existing order_info on any failure.

    Returns dict with keys: sku, name, price (same shape as order_info) or None.
    """
    # Collect all modify-intent message text
    modify_texts = [row.message_text for row in queued_rows if row.message_text]
    if not modify_texts:
        return None

    combined_text = " ".join(modify_texts)
    try:
        # agentic-rag-retry-loop (ticket 2026): retrieve_with_retry wraps search_and_retrieve
        # with the bounded self-evaluate -> rewrite -> re-retrieve loop (ADR-001).
        from services.rag.pipeline import retrieve_with_retry

        result = await retrieve_with_retry(db, combined_text, intent="INFO_QUERY")
        if result.declined or not result.citations:
            return None

        top = result.citations[0]
        # Fetch actual product from DB to get correct price and product_id.
        # This ensures state_freshness_validator has all required fields.
        from sqlalchemy import select as sa_select

        from models.schema import Product

        existing = state.get("order_info") or {}
        if isinstance(top, dict):
            product_id = top.get("product_id")
            sku = top.get("sku", "")
            name = top.get("name", "")
        else:
            product_id = getattr(top, "product_id", None)
            sku = getattr(top, "sku", "")
            name = getattr(top, "name", "")
        price = None
        if product_id:
            product_row = (
                await db.execute(sa_select(Product).where(Product.id == product_id))
            ).scalar_one_or_none()
            price = float(product_row.price) if product_row else None
        return {
            "product_id": str(product_id) if product_id else None,
            "sku": sku,
            "name": name,
            "price": price or existing.get("price"),
            "approved_price": price or existing.get("approved_price"),
            "quantity": existing.get("quantity", 1),
            "status": "pending",
        }
    except Exception as exc:
        logger.warning("SC08: _resolve_new_product_from_modify failed: %s", exc)
        return None


async def queue_consumer_node(state: AgentState, config: RunnableConfig) -> Command:
    """Processes queued messages and orphan tools after resume (Phase 9).

    1. T029: Scan/close orphan tool calls in history.
    2. T030: Drain QueuedMessage from DB within transaction.
    3. T031: Batch classify intent of drained messages.
    4. T032-T034: Route based on net intent.
    """
    db = cast("AsyncSession", config["configurable"].get("db"))
    session_id = state["session_id"]
    messages = list(state.get("messages", []))

    # --- T029: Orphan Tool Call Scanner ---
    # scan only recent 20 messages for orphan tool calls
    recent_messages = messages[-20:]
    tool_call_ids = set()
    for msg in recent_messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_call_ids.add(tc["id"])

    # Check for existing tool messages in full history
    answered_tool_call_ids = set()
    for msg in messages:
        if isinstance(msg, ToolMessage):
            answered_tool_call_ids.add(msg.tool_call_id)

    # Append synthetic ToolMessage for each orphan
    orphan_found = False
    for t_id in tool_call_ids:
        if t_id not in answered_tool_call_ids:
            messages.append(
                ToolMessage(
                    tool_call_id=t_id,
                    content="[cancelled: session resumed]",
                )
            )
            orphan_found = True

    # --- T030: QueuedMessage Drain ---
    # Fetch messages enqueued during pause
    stmt = (
        select(QueuedMessage)
        .where(QueuedMessage.session_id == session_id, QueuedMessage.processed == False)  # noqa: E712
        .order_by(QueuedMessage.received_at.asc())
        .limit(20)
    )
    result = await db.execute(stmt)
    queued_rows = result.scalars().all()

    new_human_messages = []
    queued_ids = []
    for row in queued_rows:
        new_human_messages.append(
            HumanMessage(content=f"[Customer follow-up during review]: {row.message_text}")
        )
        queued_ids.append(row.message_id)

    messages.extend(new_human_messages)

    # If no queued messages, fall through early
    if not queued_rows:
        # Check if we need to update state messages due to orphan tool calls
        update_data: dict[str, Any] = {}
        if orphan_found:
            update_data["messages"] = messages

        return Command(goto="state_freshness_validator_node", update=update_data)

    # --- T031: Batch Intent Classification (2-layer) ---
    # Layer 1: keyword heuristic — deterministic, model-size-agnostic.
    # Handles Vietnamese change-of-mind ("đổi ý rồi, lấy X đi") and cancel phrases.
    # Returns None when ambiguous → falls through to LLM.
    batch_result = _keyword_classify_batch(session_id, queued_rows)
    force_review = False

    if batch_result is None:
        # Layer 2: LLM classification with explicit few-shot examples.
        batch_text = "\n---\n".join([msg.content for msg in new_human_messages])
        try:
            # Universal JSON extractor (2026-22-8 report §4.1): native schema
            # first, schema-in-prompt json_object + repair second — provider
            # quirks no longer collapse the batch to the blind fallback.
            batch_result = await AIGateway.complete_structured(
                QueuedMessageBatch,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Classify the collective intent of customer messages sent "
                            "while their order was under admin review.\n\n"
                            "OUTPUT one of: CONFIRM, CANCEL, MODIFY_ORDER, OTHER\n\n"
                            "Rules:\n"
                            "- MODIFY_ORDER: customer wants to REPLACE product/quantity "
                            "(e.g. 'đổi ý rồi, lấy X đi', 'thay sang X', 'đặt X thay', "
                            "'lấy X thay vì Y', 'I changed my mind, get X', 'đổi sang X').\n"
                            "- CANCEL: customer wants to cancel "
                            "(e.g. 'huỷ đơn', 'không mua nữa', 'cancel').\n"
                            "- CONFIRM: customer confirms existing order OR wants to ADD an item "
                            "(e.g. 'ok', 'đồng ý', 'được rồi', 'yes', "
                            "'thêm X vào đơn luôn nhé' — ADD-ON is NOT a MODIFY).\n"
                            "- OTHER: unrelated info query (e.g. 'màu gì?', 'bao giờ giao?').\n\n"
                            f'Set session_id="{session_id}" and messages=[] in the output. '
                            "If ANY message is MODIFY_ORDER set has_modify=true. "
                            "If ANY message is CANCEL set has_cancel=true (highest priority)."
                        ),
                    },
                    {"role": "user", "content": batch_text},
                ],
                model="light-chat",
            )
            if settings.INTENT_TRACKING_V3_ENABLED:
                batch_result, force_review = _postvalidate_llm_batch(batch_result, queued_rows)
        except Exception as e:
            logger.error(f"Batch classification failed for {session_id}: {e}")
            if settings.INTENT_TRACKING_V3_ENABLED:
                batch_result = QueuedMessageBatch(session_id=session_id, messages=[])
                force_review = True
            else:
                batch_result = QueuedMessageBatch(
                    session_id=session_id, messages=[], has_confirm=True
                )

    # --- T032-T034: Routing ---
    # Mark messages as processed in DB (atomic UPDATE)
    await db.execute(
        update(QueuedMessage)
        .where(QueuedMessage.message_id.in_(queued_ids))
        .values(processed=True)
    )
    await db.flush()

    update_payload = {"messages": messages}

    # NQ2-fix: NEGOTIATION+CANCEL → re-HITL with proposed_price (before plain CANCEL check).
    # "bớt cho tôi còn 27.9tr thì lấy, không thì hủy" → extract price, ask admin to decide.
    if batch_result.has_negotiation:
        current_order = state.get("order_info") or {}
        proposed_price = _extract_proposed_price(queued_rows)
        new_escalation_count = state.get("hitl_escalation_count", 0) + 1
        if proposed_price and current_order.get("product_id"):
            negotiation_order_info = {
                **current_order,
                "approved_price": proposed_price,  # pre-fill admin's approved price
                "status": "pending",
            }
            logger.info(
                "NQ2: NEGOTIATION re-HITL: proposed_price=%s for sku=%s",
                proposed_price,
                current_order.get("sku"),
            )
            update_payload.update(
                {
                    "hitl_escalation_count": new_escalation_count,
                    "hitl_triggered": False,
                    "hitl_pause_id": None,
                    "hitl_approved": False,
                    "order_info": negotiation_order_info,
                }
            )
            return Command(goto="hitl_guard_node", update=update_payload)
        else:
            # Could not extract price → fall through to plain CANCEL
            logger.warning(
                "NQ2: negotiation detected but could not extract proposed_price; cancelling."
            )
            return Command(goto="cancellation_node", update=update_payload)

    # T032: CANCEL override (highest priority)
    if batch_result.has_cancel:
        return Command(goto="cancellation_node", update=update_payload)

    # T033: MODIFY_ORDER re-pause
    if batch_result.has_modify:
        new_escalation_count = state.get("hitl_escalation_count", 0) + 1
        current_order = state.get("order_info") or {}

        # SC3-fix: pure qty change (no product name replacement) → skip RAG entirely.
        # Only run RAG when a product NAME change was detected (_MODIFY_PATTERNS matched).
        if batch_result.has_qty_change and not batch_result.has_product_change:
            new_qty = _extract_quantity(queued_rows)
            if new_qty and current_order.get("product_id"):
                modify_order_info = {**current_order, "quantity": new_qty}
                logger.info(
                    "SC3: pure QTY_CHANGE (no product change), new_qty=%s for sku=%s",
                    new_qty,
                    current_order.get("sku"),
                )
            else:
                modify_order_info = current_order or None
        else:
            # Product name change requested → run RAG to find new product
            modify_order_info = await _resolve_new_product_from_modify(queued_rows, db, state)

            # SC3-fix: if modify_order_info resolves to the SAME product as current order_info,
            # treat as a quantity change. Extract the new quantity from the message text.
            if modify_order_info and modify_order_info.get("sku") == current_order.get("sku"):
                new_qty = _extract_quantity(queued_rows)
                modify_order_info = {
                    **current_order,
                    "quantity": new_qty or current_order.get("quantity", 1),
                }
                logger.info(
                    "SC3: RAG returned same product, treating as QTY_CHANGE, new_qty=%s",
                    new_qty,
                )
            elif not modify_order_info:
                # RAG couldn't resolve — fallback to qty change if detected
                new_qty = _extract_quantity(queued_rows)
                if new_qty and current_order.get("product_id"):
                    modify_order_info = {**current_order, "quantity": new_qty}
                    logger.info("SC3: RAG miss, QTY_CHANGE fallback, new_qty=%s", new_qty)

        update_payload.update(
            {
                "hitl_escalation_count": new_escalation_count,
                "hitl_triggered": False,
                "hitl_pause_id": None,
                # Reset approval gate so hitl_guard_node re-evaluates (not skip to answer_node)
                "hitl_approved": False,
            }
        )
        if modify_order_info:
            update_payload["order_info"] = modify_order_info
            new_pname = (
                modify_order_info.get("name") or modify_order_info.get("sku") or "sản phẩm mới"
            )
            modify_ack = (
                f"Dạ shop đã ghi nhận bạn đổi ý sang **{new_pname}**. "
                f"Yêu cầu đặt hàng mới đang được chuyển cho nhân viên xác nhận ngay ạ!"
            )
            update_payload["response"] = modify_ack
            update_payload["messages"] = [*messages, AIMessage(content=modify_ack)]
            logger.info(
                "SC08/SC3: MODIFY resolved: sku=%s qty=%s",
                modify_order_info.get("sku"),
                modify_order_info.get("quantity"),
            )

        return Command(goto="hitl_guard_node", update=update_payload)

    # F2 guard (v3-0 P1): unclassifiable message(s) while the order was under
    # review → re-pause for human review with order_info UNCHANGED (no RAG
    # product swap), instead of falling through to an implicit CONFIRM.
    if force_review:
        update_payload.update(
            {
                "hitl_escalation_count": state.get("hitl_escalation_count", 0) + 1,
                "hitl_triggered": False,
                "hitl_pause_id": None,
                "hitl_approved": False,
            }
        )
        logger.warning(
            "F2 guard: forcing human re-review for %s (unclassifiable queued message)",
            session_id,
        )
        return Command(goto="hitl_guard_node", update=update_payload)

    # ADD-ON check: if customer added an extra item alongside original order
    addon_rows = [r for r in queued_rows if _ADD_ON_PATTERNS.search(r.message_text)]
    if addon_rows and not batch_result.has_modify and not batch_result.has_cancel:
        addon_info = await _resolve_new_product_from_modify(addon_rows, db, state)
        if addon_info and addon_info.get("product_id"):
            current_order = dict(state.get("order_info") or {})
            existing_items = list(current_order.get("items") or [])
            if not existing_items and current_order.get("product_id"):
                existing_items.append(
                    {
                        "product_id": current_order["product_id"],
                        "product_name": current_order.get("name"),
                        "sku": current_order.get("sku"),
                        "quantity": current_order.get("quantity", 1),
                        "unit_price": current_order.get("price", 0),
                    }
                )
            addon_qty = _extract_quantity(addon_rows) or 1
            existing_items.append(
                {
                    "product_id": addon_info["product_id"],
                    "product_name": addon_info.get("name"),
                    "sku": addon_info.get("sku"),
                    "quantity": addon_qty,
                    "unit_price": addon_info.get("price", 0),
                }
            )
            current_order["items"] = existing_items
            current_order["quantity"] = sum(int(i.get("quantity", 1)) for i in existing_items)
            update_payload["order_info"] = current_order
            logger.info(
                "queue_consumer: ADD-ON resolved: added %s x%s to items",
                addon_info.get("name"),
                addon_qty,
            )

    # SC5-fix: INFO_QUERY fallthrough — questions about product specs/policy.
    # Collect all question texts and store for answer_node to append to the order confirmation.
    info_questions = [
        row.message_text for row in queued_rows if _INFO_QUERY_PATTERNS.search(row.message_text)
    ]
    if info_questions:
        combined = " | ".join(info_questions)
        update_payload["pending_info_questions"] = combined
        logger.info(
            "SC5: %d INFO question(s) stored for answer_node: %r",
            len(info_questions),
            combined[:80],
        )

    # T034: Fallthrough to freshness check
    return Command(goto="state_freshness_validator_node", update=update_payload)
