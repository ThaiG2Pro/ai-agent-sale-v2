"""Why this exists: queue_consumer_node (700 lines) mixed its routing with ~300 lines
of Vietnamese keyword patterns and batch classification.
What it does: classifies messages the customer sent while an order was paused for
review — layer 1 deterministic keyword heuristic, plus the post-validator that
sanitizes the LLM layer's labels (F2 guard) — and extracts quantity / proposed price.
"""

from __future__ import annotations

import logging
import re

from services.hitl.schemas import QueuedMessageBatch, QueueIntentResult

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
    """F2 guard: sanitize the LLM batch and re-derive routing flags.

    Returns (sanitized batch, force_review). force_review=True means at least
    one message carried an out-of-enum label the keyword patterns could not
    resolve — the caller must re-pause for human review rather than let the
    turn fall through to an implicit CONFIRM.
    """
    text_by_id = {str(r.message_id): r.message_text for r in rows}
    force_review = False
    has_cancel = False
    has_modify = False
    has_qty_change = batch.has_qty_change
    has_product_change = batch.has_product_change

    for msg in batch.messages:
        intent = (msg.intent or "").strip().upper()
        if intent not in _ALLOWED_QUEUE_INTENTS:
            text = text_by_id.get(str(msg.message_id), msg.text or "")
            if _CANCEL_PATTERNS.search(text):
                intent = "CANCEL"
            elif _ADD_ON_PATTERNS.search(text):
                intent = "CONFIRM"
            elif _MODIFY_PATTERNS.search(text) or _QTY_CHANGE_PATTERNS.search(text):
                intent = "MODIFY_ORDER"
            elif _INFO_QUERY_PATTERNS.search(text):
                intent = "OTHER"
            else:
                intent = "OTHER"
                force_review = True
            logger.warning(
                "F2 guard: out-of-enum LLM label %r remapped to %s for %r",
                msg.intent,
                intent,
                text[:60],
            )
            msg.intent = intent
        if intent == "CANCEL":
            has_cancel = True
        elif intent == "MODIFY_ORDER":
            has_modify = True

    # Re-derive routing flags from per-message intents — small models set the
    # top-level has_* booleans inconsistently with their own message labels.
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
