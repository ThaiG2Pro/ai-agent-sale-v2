"""
Why this exists: v3-0 P1 (T03/T06/T08) — the router was stateless per turn, so a
180° intent flip ("đặt cái này đi" → "thôi để xem thêm") wiped an in-flight
ORDER_PLACEMENT. This module makes intent a dialogue *state*: a deterministic
transition table decides whether to honor a newly classified intent or stick
with the previous one. Pure Python — 0 extra LLM calls per turn.
What it does: hesitation-signal detection, multi-intent hard priority
(CANCEL > COMPLAINT > NEGOTIATION > ORDER > INFO/PRICING), and sticky-intent
transition logic with confidence hysteresis (accept expected transitions,
strange jumps need >=0.7, any differing intent <0.5 keeps the previous one,
escape hatch after 2 consecutive disagreements).
"""

from __future__ import annotations

import re

from core.agent.state import IntentClassification, IntentEnum

# Hesitation / defer signals (T03 rec 2): deterministic flags for a probable
# flip out of an in-flight order — "thôi", "để xem thêm", "khoan đã", "chưa
# vội"... Standalone "thôi" only (word "thôi" also appears in benign phrases
# like "thôi được"), longer phrases matched anywhere.
_HESITATION_PATTERNS = re.compile(
    r"^\s*thôi[.!,\s]*$"
    r"|thôi\s*(để|khỏi|đừng)"
    r"|để\s*(xem|suy\s*nghĩ|nghĩ|tính|cân\s*nhắc)\s*(thêm|lại|đã|xíu|chút)?"
    r"|khoan\s*(đã|nhé|hẵng)?"
    r"|chưa\s*vội|từ\s*từ\s*đã|suy\s*nghĩ\s*(thêm|đã)"
    r"|let\s*me\s*think|not\s*now|hold\s*on|wait\s*a\s*(sec|moment)",
    re.IGNORECASE | re.UNICODE,
)

# Only treat hesitation as a flip when a transactional intent is in flight.
HESITATION_FLIP_SOURCES: frozenset[str] = frozenset(
    {IntentEnum.ORDER_PLACEMENT.value, IntentEnum.NEGOTIATION.value}
)

# Multi-intent hard priority (T06, fix H6). Lower rank = higher priority.
# Intents absent from the list share the lowest rank (never outrank listed ones).
_INTENT_PRIORITY: dict[IntentEnum, int] = {
    IntentEnum.CANCEL: 0,
    IntentEnum.COMPLAINT: 1,
    IntentEnum.NEGOTIATION: 2,
    IntentEnum.ORDER_PLACEMENT: 3,
    IntentEnum.INFO_QUERY: 4,
    IntentEnum.PRICING: 4,
    IntentEnum.COMPARISON: 4,
    IntentEnum.AVAILABILITY: 4,
}
_DEFAULT_RANK = 5

# Transition table (T03 option 3): from a transactional in-flight intent, a
# SMALLTALK classification is a "strange jump" — small models drift there on
# short/elliptical turns. Everything else (ORDER→CANCEL, ORDER→NEGOTIATION,
# ORDER→INFO_QUERY "nó có màu gì?", COMPLAINT→ORDER new-order-after-complaint)
# is an expected conversational move.
_STRANGE_TRANSITIONS: dict[str, frozenset[IntentEnum]] = {
    IntentEnum.ORDER_PLACEMENT.value: frozenset({IntentEnum.SMALLTALK}),
    IntentEnum.NEGOTIATION.value: frozenset({IntentEnum.SMALLTALK}),
    IntentEnum.COMPLAINT.value: frozenset({IntentEnum.SMALLTALK}),
}

# Confidence hysteresis thresholds (T03: switch at >=0.7, keep at <0.5,
# in-between strange jumps degrade to FOLLOW_UP of the previous intent).
SHIFT_ACCEPT_CONFIDENCE = 0.7
SHIFT_KEEP_CONFIDENCE = 0.5
# Escape hatch: after this many consecutive suppressed disagreements, accept
# the new intent (guards against sticky-wrong state).
MAX_DISAGREEMENTS = 2


def is_hesitation(text: str) -> bool:
    """True when the message carries a hesitation/defer signal ("thôi", "khoan đã"...)."""
    return bool(_HESITATION_PATTERNS.search(text or ""))


def normalize_priority(classification: IntentClassification) -> IntentClassification:
    """Reorder primary/secondary intents by hard priority (T06, fix H6).

    If any secondary intent outranks the primary (e.g. LLM put CANCEL in
    secondary_intents), promote it: the highest-priority intent becomes
    primary, all others become secondary (order preserved otherwise).
    """
    all_intents = [classification.primary_intent, *classification.secondary_intents]
    if len(all_intents) < 2:
        return classification

    best = min(all_intents, key=lambda i: _INTENT_PRIORITY.get(i, _DEFAULT_RANK))
    if best == classification.primary_intent:
        return classification

    remaining = [i for i in all_intents if i != best]
    return classification.model_copy(
        update={"primary_intent": best, "secondary_intents": remaining}
    )


def apply_transition(
    previous_intent: str | None,
    classification: IntentClassification,
    disagreement_count: int,
) -> tuple[IntentClassification, int]:
    """Decide whether to honor the newly classified intent or stick (T03 option 3).

    Returns (possibly-overridden classification, new disagreement count).
    Rules, in order:
    1. No previous intent, or same intent → accept, reset counter.
    2. Escape hatch: 2 consecutive suppressed disagreements → accept the new
       intent (the customer really did move on).
    3. confidence < 0.5 → keep the previous intent (hysteresis; the new label
       is demoted to secondary so it survives for the next turn).
    4. Strange jump (see _STRANGE_TRANSITIONS) with confidence < 0.7 → treat
       as FOLLOW_UP of the previous intent.
    5. Otherwise (expected transition, or confident strange jump) → accept
       with intent_shift=True.
    """
    new_intent = classification.primary_intent
    if previous_intent is None or new_intent.value == previous_intent:
        return classification.model_copy(update={"intent_shift": False}), 0

    if disagreement_count + 1 >= MAX_DISAGREEMENTS:
        return classification.model_copy(update={"intent_shift": True}), 0

    try:
        previous_enum = IntentEnum(previous_intent)
    except ValueError:
        return classification.model_copy(update={"intent_shift": True}), 0

    if classification.confidence < SHIFT_KEEP_CONFIDENCE:
        secondaries = [new_intent, *classification.secondary_intents]
        secondaries = [i for i in dict.fromkeys(secondaries) if i != previous_enum]
        return (
            classification.model_copy(
                update={
                    "primary_intent": previous_enum,
                    "secondary_intents": secondaries,
                    "intent_shift": False,
                }
            ),
            disagreement_count + 1,
        )

    strange = _STRANGE_TRANSITIONS.get(previous_intent, frozenset())
    if new_intent in strange and classification.confidence < SHIFT_ACCEPT_CONFIDENCE:
        return (
            classification.model_copy(
                update={
                    "primary_intent": IntentEnum.FOLLOW_UP,
                    "secondary_intents": [new_intent],
                    "intent_shift": False,
                }
            ),
            disagreement_count + 1,
        )

    return classification.model_copy(update={"intent_shift": True}), 0


def extract_phone(text: str) -> str | None:
    """Extracts and normalizes a Vietnamese 10-digit mobile number from text."""
    if not text:
        return None
    patterns = [
        r"(?:sđt|số điện thoại|phone|tel|đt)?\s*(?:(?:\+?84|\b0)\s*[35789](?:[\s.-]?[0-9]){8})\b",
        r"\b(?:(?:\+?84|\b0)\s*[35789](?:[\s.-]?[0-9]){8})\b",
        r"\b(0[35789][0-9]{8})\b",
        r"\b(0[1-9][0-9]{8})\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            raw = m.group(0)
            digits = re.sub(r"\D", "", raw)
            if digits.startswith("84") and len(digits) == 11:
                digits = "0" + digits[2:]
            if len(digits) == 10 and digits.startswith("0"):
                return digits
    return None


def extract_address(text: str) -> str | None:
    """Extracts fulfillment delivery address from customer message."""
    if not text:
        return None

    # 1. Explicit keyword prefix: "địa chỉ: ...", "ở ...", "giao tại...", "giao về...", "ship đến..."
    m = re.search(
        r"(?:địa chỉ|đc|đ/c|ở|tại|giao (?:về|đến|tới)|nhận tại|ship (?:đến|về|tới))\s*[:\s-]\s*([^,\n]+(?:,[^,\n]+)*)",
        text,
        re.IGNORECASE,
    )
    if m:
        addr = m.group(1).strip()
        # Remove phone from address if included
        addr = re.sub(
            r"(?:sđt|số điện thoại|phone|tel|đt)?\s*(?:\+?84|0)[35789\d][\d\s.-]{7,12}\b",
            "",
            addr,
            flags=re.IGNORECASE,
        ).strip()
        addr = re.sub(r"^[,\s.-]+|[,\s.-]+$", "", addr)
        if len(addr) >= 3:
            return addr

    # 2. Contains location/administrative markers
    loc_kw = r"\b(?:quận|huyện|phường|xã|đường|phố|thành phố|tp|tỉnh|hà nội|hcm|hồ chí minh|đà nẵng|hải phòng|cần thơ|bình dương|đồng nai|nguyễn |lê |trần |hai bà trưng|hoàng diệu|cầu giấy|hoàn kiếm|ba đình|đống đa|thanh xuân|bình thạnh|gò vấp|tân bình|thủ đức)\b"
    if re.search(loc_kw, text, re.IGNORECASE):
        cleaned = re.sub(
            r"(?:sđt|số điện thoại|phone|tel|đt)?\s*(?:\+?84|0)[35789\d][\d\s.-]{7,12}\b",
            "",
            text,
            flags=re.IGNORECASE,
        ).strip()
        cleaned = re.sub(r"^[,\s.-]+|[,\s.-]+$", "", cleaned)
        if len(cleaned) >= 4:
            return cleaned

    # 3. Starts with house number + street (e.g., "123 Lê Lợi", "45/2 Nguyễn Huệ")
    m_num = re.search(r"^\s*(\d+[A-Za-z]?/\d+[A-Za-z]?|\d+[A-Za-z]?)\s+([A-Za-zÀ-ỹ\s,.-]+)$", text)
    if m_num:
        return text.strip()

    return None
