"""queue_consumer_node — processes queued messages after unpause.

Why: Central integration point after HITL resume. Processes customer messages
received while paused, ensures history consistency by closing orphan tool calls,
and routes to the next step (execute, cancel, or re-pause).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, cast

import litellm
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command
from sqlalchemy import select, update

from core.config import settings
from models.schema import QueuedMessage
from services.hitl.schemas import QueuedMessageBatch, QueueIntentResult

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.agent.state import AgentState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword heuristic — deterministic, model-size-agnostic, language-aware.
# Works correctly in dev (small model) AND production (large model agrees).
# ---------------------------------------------------------------------------
_MODIFY_PATTERNS = re.compile(
    r"đổi\s*ý|thay\s*(đổi|sang|cho)|đặt\s+.+?\s*thay|lấy\s+.+?\s*(thay|đi|nhé|đó|này)|"
    r"đổi\s*sang|đổi\s*qua|muốn\s*(đổi|thay)|changed?\s*my\s*mind|switch\s*to|instead",
    re.IGNORECASE | re.UNICODE,
)
_CANCEL_PATTERNS = re.compile(
    r"huỷ|hủy|không\s*(mua|đặt|lấy)\s*nữa|thôi\s*(không|rồi)|cancel|bỏ\s*đơn",
    re.IGNORECASE | re.UNICODE,
)
_CONFIRM_PATTERNS = re.compile(
    r"^(ok|oke|okay|được(\s*rồi)?|đồng\s*ý|cứ\s*đặt|yes|xác\s*nhận|chốt)[.!,\s]*$",
    re.IGNORECASE | re.UNICODE,
)
# ADD_ON: customer wants to add a new product alongside the existing order,
# NOT replace it. "thêm X vào đơn" → CONFIRM (keep original) not MODIFY_ORDER.
# NQ3-FIX: "lấy thêm X nhé" must match here BEFORE _MODIFY_PATTERNS catches "lấy...nhé".
_ADD_ON_PATTERNS = re.compile(
    r"thêm\s+\S.{2,}\s+(vào\s*đơn|luôn\s*nhé|vào\s*giỏ|cùng\s*đơn|thêm\s*vào)"
    r"|cũng\s+(lấy|mua|đặt)\s+thêm"
    r"|lấy\s+thêm\s+.{3,}(?:nhé|đi|thôi|luôn)"  # NQ3: "lấy thêm 1 sạc Anker nhé"
    r"|order\s+also\s+|add\s+.+\s+to\s+(the\s+)?order",
    re.IGNORECASE | re.UNICODE,
)
# SC3-FIX: Quantity change — customer changes count on the SAME product.
# Must come AFTER _MODIFY_PATTERNS check (MODIFY takes higher priority when product also changes).
_QTY_CHANGE_PATTERNS = re.compile(
    r"lấy\s+(?:cho\s+tôi\s+)?(\d+)\s*(cái|chiếc|máy)"
    r"|(\d+)\s*(cái|chiếc|máy)\s*(nhé|đi|thôi)"
    r"|số\s*lượng\s*(\d+)"
    r"|mua\s+(\d+)\s*(cái|chiếc)"
    r"|đặt\s+(?:cho\s+tôi\s+)?(\d+)\s*(cái|chiếc|máy)",
    re.IGNORECASE | re.UNICODE,
)
# SC5-FIX: INFO_QUERY guard — messages that are clearly QUESTIONS about the product,
# not intent-to-replace or cancel. Must be checked BEFORE LLM to prevent small-model
# misclassification (e.g. "bao nhiêu Watt" → MODIFY_ORDER picking "Anker 140W").
_INFO_QUERY_PATTERNS = re.compile(
    r"\?$"  # ends with question mark
    r"|bao\s*nhiêu"  # "bao nhiêu Watt/tiền/..."
    r"|có\s*(không|được|kèm|tặng|hỗ\s*trợ)"  # "có ... không/được"
    r"|(nhỉ|hả|ha|không\s*nhỉ)\s*\??$"  # ends with discourse particle
    r"|dùng\s*(được|sạc|pin|ram|ổ|màn)"  # technical questions
    r"|giao\s*(hàng|trong)\s*(bao\s*lâu|mấy\s*ngày)"  # delivery questions
    r"|bảo\s*hành",  # warranty questions
    re.IGNORECASE | re.UNICODE,
)
# NQ2-FIX: Negotiation with conditional cancel.
# "bớt cho tôi còn 27.9tr được thì lấy, không thì hủy" → detect price proposal.
# These messages combine NEGOTIATION price offer with conditional CANCEL.
# Strategy: if NEGOTIATION detected alongside CANCEL, treat as re-HITL with proposed_price.
_NEGOTIATION_PATTERNS = re.compile(
    r"bớt\s*(cho\s*tôi|giá|đi)?"  # "bớt cho tôi", "bớt giá"
    r"|giảm\s*(giá|còn|xuống)"  # "giảm giá", "giảm còn"
    r"|còn\s+\d"  # "còn 27tr" (price reduction)
    r"|\d[\d.,]*\s*(tr|triệu|M)\s*"
    r"(được\s*(thì|là)|thì\s*(tôi\s*)?(lấy|ok|được))"  # "27tr được thì lấy"
    r"|mặc\s*cả|thương\s*lượng|thêm\s*khuyến\s*mãi"  # negotiation terms
    r"|chỗ\s*(khác|kia)\s*(bán|có)\s*(có\s*)?\d"  # competitor price reference
    r"|negotiate|discount\s*to",
    re.IGNORECASE | re.UNICODE,
)
# NQ2-FIX: Extract proposed price from negotiation text.
# "còn 27.9tr" / "27tr thì tôi lấy" / "giảm xuống 28 triệu"
_PRICE_EXTRACT = re.compile(
    r"(?:còn|xuống|giá|bớt\s+(?:cho\s+tôi\s+)?còn?)\s*(\d+(?:[.,]\d+)?)\s*(tr|triệu|M)\b"
    r"|(\d+(?:[.,]\d+)?)\s*(tr|triệu|M)\s+(?:được\s*(?:thì|là)|thì\s*(?:tôi\s*)?(?:lấy|ok|đồng\s*ý))",
    re.IGNORECASE | re.UNICODE,
)


def _keyword_classify_batch(
    session_id: str,
    rows: list,
) -> QueuedMessageBatch | None:
    """Fast deterministic pre-classifier. Returns None when ambiguous (→ fall back to LLM).

    Strategy:
    - If ANY message matches MODIFY_ORDER or QTY_CHANGE keywords → has_modify=True (skip LLM).
    - If ANY message matches CANCEL keywords → has_cancel=True (skip LLM).
    - If ALL messages match CONFIRM keywords → has_confirm=True (skip LLM).
    - If ALL messages match INFO_QUERY keywords → has_info=True (skip LLM, answer questions).
    - NQ2: If CANCEL + NEGOTIATION coexist → has_negotiation=True (re-HITL with proposed_price).
    - Otherwise return None → caller uses LLM.

    Priority order per message:
    CANCEL > ADD_ON > MODIFY > QTY_CHANGE > INFO_QUERY > CONFIRM > OTHER.
    INFO_QUERY guard (SC5-fix): question-style messages are forced to OTHER/INFO before the LLM
    can misclassify them as MODIFY_ORDER (e.g. "bao nhiêu Watt" → picks an unrelated product).
    """
    results: list[QueueIntentResult] = []
    has_cancel = False
    has_modify = False
    has_qty_change = False
    has_product_change = False  # SC3: tracks if a product NAME change was requested (not just qty)
    has_negotiation = False  # NQ2: price negotiation detected
    all_confirm = True
    all_info = True

    for row in rows:
        text = row.message_text
        msg_id = str(row.message_id)
        # NQ2-fix: Check negotiation BEFORE cancel to detect conditional-cancel pattern.
        # "27.9tr được thì lấy, không thì hủy" → NEGOTIATION wins over CANCEL.
        if _NEGOTIATION_PATTERNS.search(text) and _CANCEL_PATTERNS.search(text):
            intent, conf = "NEGOTIATION", 0.90
            has_negotiation = True
            # Still record cancel intent so routing can handle "reject → cancel" path
            has_cancel = True
            all_confirm = False
            all_info = False
            logger.info("NQ2: NEGOTIATION+CANCEL detected (propose price re-HITL): %r", text[:60])
        elif _CANCEL_PATTERNS.search(text):
            intent, conf = "CANCEL", 0.95
            has_cancel = True
            all_confirm = False
            all_info = False
        elif _ADD_ON_PATTERNS.search(text):
            intent, conf = "CONFIRM", 0.85
            all_info = False
            logger.info("queue_consumer: ADD_ON detected (treated as CONFIRM): %r", text[:60])
        elif _MODIFY_PATTERNS.search(text):
            intent, conf = "MODIFY_ORDER", 0.92
            has_modify = True
            has_product_change = True
            all_confirm = False
            all_info = False
        elif _QTY_CHANGE_PATTERNS.search(text):
            # SC3-fix: quantity change on the SAME product → MODIFY_ORDER (re-HITL for review).
            intent, conf = "MODIFY_ORDER", 0.88
            has_qty_change = True
            has_modify = True
            all_confirm = False
            all_info = False
            logger.info("queue_consumer: QTY_CHANGE detected as MODIFY_ORDER: %r", text[:60])
        elif _INFO_QUERY_PATTERNS.search(text):
            # SC5-fix: question about product specs/policy → classify as OTHER so the
            # order flow is not interrupted. Answers will be fetched after order confirmation.
            intent, conf = "OTHER", 0.85
            all_confirm = False
            logger.info("queue_consumer: INFO_QUERY detected (forced OTHER): %r", text[:60])
        elif _CONFIRM_PATTERNS.match(text.strip()):
            intent, conf = "CONFIRM", 0.90
            all_info = False
        else:
            intent, conf = "OTHER", 0.50
            all_confirm = False
            all_info = False
        results.append(
            QueueIntentResult(message_id=msg_id, text=text, intent=intent, confidence=conf)
        )

    # Only skip LLM when we have strong keyword signal
    has_strong_signal = (
        has_cancel
        or has_modify
        or has_negotiation
        or (all_confirm and results)
        or (all_info and results)
    )
    if has_strong_signal:
        batch = QueuedMessageBatch(session_id=session_id, messages=results)
        batch.has_cancel = has_cancel
        batch.has_modify = has_modify
        batch.has_qty_change = has_qty_change
        batch.has_product_change = has_product_change
        batch.has_confirm = all_confirm and not has_cancel and not has_modify
        batch.has_info = all_info and not has_cancel and not has_modify
        batch.has_negotiation = has_negotiation
        logger.info(
            "queue_consumer keyword classify: "
            "cancel=%s modify=%s confirm=%s info=%s qty_change=%s negotiation=%s",
            has_cancel,
            has_modify,
            batch.has_confirm,
            batch.has_info,
            has_qty_change,
            has_negotiation,
        )
        return batch

    return None  # ambiguous → use LLM


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
        from services.rag.pipeline import search_and_retrieve

        result = await search_and_retrieve(db, combined_text, intent="INFO_QUERY")
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


def _extract_quantity(queued_rows: list) -> int | None:
    """SC3-fix: extract the first numeric quantity from queued messages.

    Matches patterns like "lấy 2 cái", "lấy cho tôi 2 cái", "2 chiếc nhé", "mua 3 cái".
    Returns the integer quantity or None if not found.
    """
    qty_re = re.compile(
        r"(?:lấy|mua|đặt)(?:\s+cho\s+tôi)?\s+(\d+)\s*(?:cái|chiếc|máy)"
        r"|(\d+)\s*(?:cái|chiếc|máy)\s*(?:nhé|đi|thôi)",
        re.IGNORECASE | re.UNICODE,
    )
    for row in queued_rows:
        m = qty_re.search(row.message_text)
        if m:
            qty_str = m.group(1) or m.group(2)
            if qty_str and qty_str.isdigit():
                return int(qty_str)
    return None


def _extract_proposed_price(queued_rows: list) -> float | None:
    """NQ2-fix: extract customer's proposed price from negotiation messages.

    Matches patterns like "còn 27.9tr", "27tr được thì lấy", "giảm xuống 28 triệu".
    Returns price in VND (multiplied by 1_000_000) or None if not found.
    """
    for row in queued_rows:
        m = _PRICE_EXTRACT.search(row.message_text)
        if m:
            # Group 1+2 → "còn/xuống X tr" form; Group 3+4 → "X tr được thì" form
            price_str = m.group(1) or m.group(3)
            if price_str:
                price_str = price_str.replace(",", ".")
                try:
                    price_val = float(price_str)
                    # Values like 27.9 are in millions (triệu), convert to VND
                    if price_val < 10_000:
                        price_val *= 1_000_000
                    logger.info(
                        "NQ2: extracted proposed_price=%s from %r",
                        price_val,
                        row.message_text[:60],
                    )
                    return price_val
                except ValueError:
                    pass
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

    if batch_result is None:
        # Layer 2: LLM classification with explicit few-shot examples.
        batch_text = "\n---\n".join([msg.content for msg in new_human_messages])
        try:
            response = await litellm.acompletion(
                model=settings.LIGHT_CHAT_MODEL,
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
                            "If ANY message is MODIFY_ORDER set has_modify=true. "
                            "If ANY message is CANCEL set has_cancel=true (highest priority)."
                        ),
                    },
                    {"role": "user", "content": batch_text},
                ],
                response_format=QueuedMessageBatch,
            )
            content = response.choices[0].message.content
            if isinstance(content, str):
                batch_result = QueuedMessageBatch.model_validate_json(content)
            else:
                batch_result = QueuedMessageBatch.model_validate(content)

        except Exception as e:
            logger.error(f"Batch classification failed for {session_id}: {e}")
            batch_result = QueuedMessageBatch(session_id=session_id, messages=[], has_confirm=True)

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
            logger.info(
                "SC08/SC3: MODIFY resolved: sku=%s qty=%s",
                modify_order_info.get("sku"),
                modify_order_info.get("quantity"),
            )

        return Command(goto="hitl_guard_node", update=update_payload)

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
