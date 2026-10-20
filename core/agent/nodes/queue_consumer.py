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

from core.config import settings
from models.schema import QueuedMessage
from services.ai import AIGateway
from services.hitl.schemas import QueuedMessageBatch, QueueIntentResult

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.agent.state import AgentState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Strict Deterministic Tokens & Phrases (Fast-path pre-classifier)
# Fast path handles single-word confirmations and simple cancellations.
# ---------------------------------------------------------------------------
_FAST_CONFIRM_TOKENS = frozenset(
    {"ok", "oke", "okay", "yes", "chốt", "đồng ý", "xác nhận", "cứ đặt", "được rồi", "được"}
)
_FAST_CANCEL_SUBSTRINGS = (
    "huỷ",
    "hủy",
    "không mua nữa",
    "không đặt nữa",
    "không lấy nữa",
    "thôi không",
    "cancel",
    "bỏ đơn",
)
_FAST_MODIFY_SUBSTRINGS = (
    "đổi ý",
    "thay sang",
    "đặt lại",
    "thay cho",
    "đổi qua",
    "muốn đổi",
    "changed my mind",
    "instead",
    "switch to",
)


# F2 guard (v3-0 P1): the ONLY labels the LLM branch may emit. Small models
# (qwen3-1.7b) have emitted out-of-enum labels like FOLLOW_UP for change-of-mind
# messages ("Tôi đổi ý rồi, lấy Xiaomi đi"), which silently fell through and
# confirmed the OLD order. Out-of-enum labels are re-checked against the keyword
# patterns; unresolvable ones force a human re-review instead of a confirm.
_ALLOWED_QUEUE_INTENTS = frozenset({"CONFIRM", "CANCEL", "MODIFY_ORDER", "NEGOTIATION", "OTHER"})


def _postvalidate_llm_batch(
    batch: QueuedMessageBatch,
    rows: list,
) -> tuple[QueuedMessageBatch, bool]:
    """F2 guard: sanitize the LLM batch and re-derive routing flags."""
    force_review = False
    has_cancel = False
    has_modify = False
    has_qty_change = batch.has_qty_change
    has_product_change = batch.has_product_change

    text_by_id = {str(r.message_id): r.message_text for r in rows}
    for msg in batch.messages:
        intent = (msg.intent or "").strip().upper()
        if intent not in _ALLOWED_QUEUE_INTENTS:
            text = (text_by_id.get(str(msg.message_id), msg.text or "")).lower()
            if any(c in text for c in _FAST_CANCEL_SUBSTRINGS):
                intent = "CANCEL"
            elif (
                any(m in text for m in _FAST_MODIFY_SUBSTRINGS)
                or " thay " in text
                or text.endswith(" thay")
            ):
                intent = "MODIFY_ORDER"
            elif any(k in text for k in _FAST_CONFIRM_TOKENS):
                intent = "CONFIRM"
            else:
                intent = "OTHER"
                force_review = True
            logger.warning(
                "F2 guard: out-of-enum LLM label %r remapped to %s",
                msg.intent,
                intent,
            )
            msg.intent = intent
        if intent == "CANCEL":
            has_cancel = True
        elif intent == "MODIFY_ORDER":
            has_modify = True

    # Re-derive routing flags from per-message intents
    batch.has_cancel = batch.has_cancel or has_cancel
    batch.has_modify = batch.has_modify or has_modify
    batch.has_qty_change = has_qty_change
    batch.has_product_change = has_product_change or has_modify
    if batch.has_cancel or batch.has_modify:
        batch.has_confirm = False
    return batch, force_review


def _keyword_classify_batch(
    session_id: str,
    rows: list,
) -> QueuedMessageBatch | None:
    """Fast deterministic pre-classifier. Returns None when ambiguous (→ fall back to LLM).

    Fast deterministic rules (Substring / Token-based, no complex regex):
    - Substring match on definite cancel phrases ('huỷ', 'không mua nữa'...) -> CANCEL
    - Substring match on definite change-of-mind phrases ('đổi ý', 'thay sang'...) -> MODIFY_ORDER
    - Exact match on confirmation tokens ('ok', 'chốt', 'đồng ý'...) -> CONFIRM
    - Everything else (complex negotiation, questions, ambiguous text) -> return None to let Light Model handle.
    """
    results: list[QueueIntentResult] = []
    has_cancel = False
    has_modify = False
    all_confirm = True

    for row in rows:
        text = (row.message_text or "").strip().lower()
        msg_id = str(row.message_id)

        # Fast cancel check (highest priority)
        if any(c_sub in text for c_sub in _FAST_CANCEL_SUBSTRINGS):
            intent, conf = "CANCEL", 0.95
            has_cancel = True
            all_confirm = False
        # Fast modify check
        elif (
            any(m_sub in text for m_sub in _FAST_MODIFY_SUBSTRINGS)
            or " thay " in text
            or text.endswith(" thay")
        ):
            intent, conf = "MODIFY_ORDER", 0.92
            has_modify = True
            all_confirm = False
        # Fast single-word / short confirmation check
        elif text in _FAST_CONFIRM_TOKENS or text.rstrip(".!,") in _FAST_CONFIRM_TOKENS:
            intent, conf = "CONFIRM", 0.95
        else:
            # Complex message (modify product details, qty change, negotiate price, questions) -> delegate to LLM
            return None

        results.append(
            QueueIntentResult(
                message_id=msg_id, text=row.message_text, intent=intent, confidence=conf
            )
        )

    if has_cancel or has_modify:
        batch = QueuedMessageBatch(session_id=session_id, messages=results)
        batch.has_cancel = has_cancel
        batch.has_modify = has_modify
        batch.has_product_change = has_modify
        batch.has_confirm = False
        return batch
    elif all_confirm and results:
        batch = QueuedMessageBatch(session_id=session_id, messages=results)
        batch.has_confirm = True
        batch.has_cancel = False
        return batch

    return None


async def _resolve_new_product_from_modify(
    queued_rows: list,
    db: AsyncSession,
    state: AgentState,
    target_product_name: str | None = None,
) -> dict | None:
    """Entity-gated product resolution for MODIFY messages.

    Why: Prevents Blind RAG from grabbing random products (e.g. SSD Samsung)
         when user message only contains conversational or quantity text.
    Invariants:
    1. Only runs RAG when target_product_name is explicitly provided.
    2. Requires similarity_score >= 0.70 to accept the resolved product.
    """
    if not target_product_name or len(target_product_name.strip()) < 2:
        logger.info("EntityGate: No explicit target_product_name provided; skipping RAG.")
        return None

    query_entity = target_product_name.strip()
    try:
        from services.rag.pipeline import retrieve_with_retry

        result = await retrieve_with_retry(db, query_entity, intent="INFO_QUERY")
        if result.declined or not result.citations:
            logger.info("EntityGate: RAG found no citations for '%s'", query_entity)
            return None

        top = result.citations[0]
        # Guard: check confidence/similarity threshold (0.70)
        sim_score = (
            top.get("similarity_score")
            if isinstance(top, dict)
            else getattr(top, "similarity_score", 0.0)
        )
        if sim_score and sim_score < settings.AGENT_CONFIDENCE_THRESHOLD:
            logger.warning(
                "EntityGate: RAG top match '%s' score %.2f < threshold %.2f; rejected.",
                top.get("name") if isinstance(top, dict) else getattr(top, "name", ""),
                sim_score,
                settings.AGENT_CONFIDENCE_THRESHOLD,
            )
            return None

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
        logger.warning("EntityGate: _resolve_new_product_from_modify failed: %s", exc)
        return None


async def _extract_quantity_and_price_via_entity(
    queued_rows: list, current_order: dict | None
) -> tuple[int | None, float | None]:
    """Ecommerce Entity Extractor: extracts quantity and proposed negotiation price using Light Model."""
    texts = [row.message_text for row in queued_rows if row.message_text]
    if not texts:
        return None, None
    combined = " ".join(texts)
    from core.agent.order_mutator import parse_order_mutation

    parsed = await parse_order_mutation(combined, current_order=current_order)
    return parsed.new_quantity, parsed.proposed_price


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
    # NQ2-fix: NEGOTIATION+CANCEL → re-HITL with proposed_price (before plain CANCEL check).
    # "bớt cho tôi còn 27.9tr thì lấy, không thì hủy" → extract price, ask admin to decide.
    if batch_result.has_negotiation:
        current_order = state.get("order_info") or {}
        _, proposed_price = await _extract_quantity_and_price_via_entity(
            queued_rows, current_order
        )
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
            new_qty, _ = await _extract_quantity_and_price_via_entity(queued_rows, current_order)
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
            # Product name change requested → extract entity using parse_order_mutation first (Entity-Gating)
            from core.agent.order_mutator import parse_order_mutation
            from core.agent.schemas.mutation import OrderActionEnum

            combined_mod_text = " ".join([r.message_text for r in queued_rows if r.message_text])
            mutation_parsed = await parse_order_mutation(
                combined_mod_text, current_order=current_order
            )

            if (
                mutation_parsed.action == OrderActionEnum.UPDATE_QUANTITY
                and mutation_parsed.new_quantity
            ):
                modify_order_info = {
                    **current_order,
                    "quantity": mutation_parsed.new_quantity,
                }
                logger.info(
                    "EntityGate: Parsed as pure QTY_CHANGE via light model, new_qty=%s for sku=%s",
                    mutation_parsed.new_quantity,
                    current_order.get("sku"),
                )
            else:
                target_pname = mutation_parsed.target_product_name
                modify_order_info = await _resolve_new_product_from_modify(
                    queued_rows, db, state, target_product_name=target_pname
                )

            # SC3-fix: if modify_order_info resolves to the SAME product as current order_info,
            # treat as a quantity change. Extract the new quantity from the message text.
            if modify_order_info and modify_order_info.get("sku") == current_order.get("sku"):
                new_qty, _ = await _extract_quantity_and_price_via_entity(
                    queued_rows, current_order
                )
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
                new_qty, _ = await _extract_quantity_and_price_via_entity(
                    queued_rows, current_order
                )
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
    # Handled via Entity Extractor (parse_order_mutation) without regex patterns
    from core.agent.order_mutator import parse_order_mutation
    from core.agent.schemas.mutation import OrderActionEnum

    if not batch_result.has_modify and not batch_result.has_cancel:
        combined_text = " ".join([r.message_text for r in queued_rows if r.message_text])
        mutation_res = await parse_order_mutation(
            combined_text, current_order=state.get("order_info")
        )

        if mutation_res.action == OrderActionEnum.ADD_ON and mutation_res.target_product_name:
            addon_info = await _resolve_new_product_from_modify(
                queued_rows, db, state, target_product_name=mutation_res.target_product_name
            )
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
                addon_qty = mutation_res.new_quantity or 1
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
                    "queue_consumer: ADD-ON resolved via entity: added %s x%s to items",
                    addon_info.get("name"),
                    addon_qty,
                )
        elif mutation_res.action == OrderActionEnum.INFO_QUERY:
            update_payload["pending_info_questions"] = combined_text
            logger.info("SC5: INFO question stored via entity: %r", combined_text[:80])

    # T034: Fallthrough to freshness check
    return Command(goto="state_freshness_validator_node", update=update_payload)
