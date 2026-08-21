"""Router node for LangGraph sales agent (T044-T045).

Why: First node in the agent graph — classifies user intent using LLM
and routes to next node based on intent category.

What: Uses AIGateway.complete (via ai_router) with response_format=IntentClassification
to classify multi-intent and route to retrieval_node, escalation_node, or
answer_node.
"""

from __future__ import annotations

import logging

from langgraph.types import Command

from core.agent.intent_transitions import (
    HESITATION_FLIP_SOURCES,
    apply_transition,
    is_hesitation,
    normalize_priority,
)
from core.agent.state import AgentState, IntentClassification, IntentEnum
from core.config import settings
from services.ai import AIGateway

logger = logging.getLogger(__name__)

# v3-0 P1 (T03): cap history context at the last 3 exchanges (6 messages) —
# small models over-anchor on long history (keep N<=3, current message LAST).
_HISTORY_MAX_MESSAGES = 6
_HISTORY_MAX_CHARS = 200

# H6 (T06): ORDER_PLACEMENT primary with an advisory secondary — queue the
# advisory part so order_execution answers it alongside the confirmation
# (reuses the SC5 pending_info_questions machinery).
_ADVISORY_SECONDARIES = frozenset(
    {IntentEnum.INFO_QUERY, IntentEnum.PRICING, IntentEnum.COMPARISON, IntentEnum.AVAILABILITY}
)


def _format_history(messages: list, current_message: str) -> str:
    """Last N prior turns as a compact context block (T03 option 1).

    Excludes the trailing copy of the current message; truncates each line.
    """
    prior = list(messages or [])
    if prior and getattr(prior[-1], "content", None) == current_message:
        prior = prior[:-1]
    lines = []
    for msg in prior[-_HISTORY_MAX_MESSAGES:]:
        content = str(getattr(msg, "content", "") or "").strip()
        if not content:
            continue
        role = "Customer" if msg.__class__.__name__ == "HumanMessage" else "Agent"
        if len(content) > _HISTORY_MAX_CHARS:
            content = content[:_HISTORY_MAX_CHARS] + "…"
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


# ── v3-0 P4 (T11 4.2/4.3): zero-LLM classification fast paths ───────────────

# 4.2 SMALLTALK fast-path — conservative gate: FULL match against this set,
# <=4 words, and no product/price/order token anywhere in the message.
_SMALLTALK_FULL_MATCHES = frozenset(
    {
        "hi",
        "hello",
        "chào",
        "chào shop",
        "chào bạn",
        "xin chào",
        "xin chào shop",
        "hi shop",
        "hello shop",
        "alo",
        "cảm ơn",
        "cảm ơn shop",
        "cám ơn",
        "thanks",
        "thank you",
        "ok",
        "oke",
        "oke shop",
        "tạm biệt",
        "bye",
    }
)
# Any of these tokens anywhere ⇒ NOT smalltalk / NOT whitelist-safe.
_BUSINESS_TOKENS = (
    "giá",
    "mua",
    "đặt",
    "hủy",
    "order",
    "sản phẩm",
    "hàng",
    "tiền",
    "triệu",
    "khiếu nại",
    "bảo hành",
    "giảm",
    "ship",
    "giao",
)


def _smalltalk_fastpath(user_msg: str) -> bool:
    msg = user_msg.strip().lower()
    if not msg or len(msg.split()) > 4:
        return False
    if any(tok in msg for tok in _BUSINESS_TOKENS):
        return False
    return msg in _SMALLTALK_FULL_MATCHES


# 4.3 keyword pre-classify whitelist — INFO_QUERY/PRICING/AVAILABILITY only
# (never escalation-worthy or order intents; those must keep the LLM path so
# HITL-pause/priority routing is never bypassed).
_WHITELIST_BLOCKERS = (
    "mua",
    "đặt",
    "hủy",
    "order",
    "khiếu nại",
    "bực",
    "tệ",
    "lỗi",
    "hoàn tiền",
    "giảm giá",
    "bớt",
    "trả giá",
    "deal",
    "thôi",
    "khoan",
)
_WHITELIST_PATTERNS: tuple[tuple[str, IntentEnum], ...] = (
    ("giá bao nhiêu", IntentEnum.PRICING),
    ("bao nhiêu tiền", IntentEnum.PRICING),
    ("giá của", IntentEnum.PRICING),
    ("báo giá", IntentEnum.PRICING),
    ("còn hàng", IntentEnum.AVAILABILITY),
    ("hết hàng chưa", IntentEnum.AVAILABILITY),
    ("có sẵn", IntentEnum.AVAILABILITY),
    ("khi nào có hàng", IntentEnum.AVAILABILITY),
    ("shop có những", IntentEnum.INFO_QUERY),
    ("bán những gì", IntentEnum.INFO_QUERY),
    ("có sản phẩm gì", IntentEnum.INFO_QUERY),
    ("thông số", IntentEnum.INFO_QUERY),
    ("cấu hình", IntentEnum.INFO_QUERY),
)


def _whitelist_classify(user_msg: str) -> IntentEnum | None:
    msg = user_msg.strip().lower()
    if not msg or any(tok in msg for tok in _WHITELIST_BLOCKERS):
        return None
    for pattern, intent in _WHITELIST_PATTERNS:
        if pattern in msg:
            return intent
    return None


# Safe fallback when the LLM returns malformed/incomplete JSON: INFO_QUERY
# routes to retrieval_node, whose confidence guards decline gracefully.
_FALLBACK_CLASSIFICATION = IntentClassification(
    primary_intent=IntentEnum.INFO_QUERY,
    secondary_intents=[],
    confidence=0.0,
    reasoning="fallback: classification failed (malformed LLM output)",
)


async def router_node(state: AgentState) -> Command:
    """Classify user intent and route to next node (T044).

    Uses economy-chat model (not light-chat due to Ollama G1 constraint).
    Returns Command with goto and state updates.
    """
    user_msg = state.get("user_message") or ""
    user_msg_lower = user_msg.lower()
    v3_enabled = settings.INTENT_TRACKING_V3_ENABLED
    previous_intent = state.get("intent") if v3_enabled else None

    cancel_keywords = [
        "hủy đơn",
        "không mua nữa",
        "hủy giúp",
        "muốn hủy",
        "cancel order",
        "thôi không mua",
        "hủy đơn hàng",
    ]
    if any(kw in user_msg_lower for kw in cancel_keywords):
        return Command(
            goto="cancellation_node",
            update={
                "intent": IntentEnum.CANCEL.value,
                "secondary_intents": [],
                "intent_confidence": 1.0,
                "intent_shift": previous_intent not in (None, IntentEnum.CANCEL.value),
                "intent_disagreement_count": 0,
            },
        )

    # v3-0 P1 (T03 rec 2, F5): hesitation/defer signal while an order-ish
    # intent is in flight → deterministic flip to CANCEL, no LLM call.
    if v3_enabled and previous_intent in HESITATION_FLIP_SOURCES and is_hesitation(user_msg):
        logger.info(
            "router_node hesitation flip: previous_intent=%s msg=%r",
            previous_intent,
            user_msg[:60],
        )
        return Command(
            goto="cancellation_node",
            update={
                "intent": IntentEnum.CANCEL.value,
                "secondary_intents": [],
                "intent_confidence": 0.9,
                "intent_shift": True,
                "intent_disagreement_count": 0,
            },
        )

    # v3-0 P4 (T11 4.2): in-graph SMALLTALK fast-path — saves both the router
    # LLM call and the answer LLM call (template branch in answer_node).
    # Checkpoint still records messages + intent (combo 1.1 stays fed).
    if settings.SMALLTALK_FASTPATH_ENABLED and _smalltalk_fastpath(user_msg):
        return Command(
            goto="answer_node",
            update={
                "intent": IntentEnum.SMALLTALK.value,
                "secondary_intents": [],
                "intent_confidence": 1.0,
                "intent_shift": previous_intent not in (None, IntentEnum.SMALLTALK.value),
                "intent_disagreement_count": 0,
                "smalltalk_fastpath": True,
            },
        )

    # v3-0 P4 (T11 4.3): keyword pre-classify whitelist — INFO_QUERY/PRICING/
    # AVAILABILITY skip the router LLM. IN-GRAPH by design (a pre-router
    # bypass was rejected: it would skip HITL-pause routing); the semantic
    # cache stays in retrieval_node and is invalidated on price/stock updates.
    if settings.PRECLASSIFY_WHITELIST_ENABLED and (wl_intent := _whitelist_classify(user_msg)):
        return Command(
            goto="retrieval_node",
            update={
                "intent": wl_intent.value,
                "secondary_intents": [],
                "intent_confidence": 0.85,
                "intent_shift": previous_intent not in (None, wl_intent.value),
                "intent_disagreement_count": 0,
            },
        )

    system_prompt = (
        "You are an intent classifier for a Vietnamese e-commerce sales agent. "
        "Classify the user message into one of these intents:\n"
        "- INFO_QUERY: asking about product features, specs, general info, browsing catalog, "
        "or asking what products are available "
        "(e.g. 'what laptops do you have?', 'bạn có gì không?', 'bạn bán gì?', "
        "'shop có sản phẩm gì?', 'cho tôi xem sản phẩm', 'tell me about X')\n"
        "- PRICING: asking about price, cost range, budget ('under X million'), discounts, "
        "or promotions (e.g. 'laptops under 25 million', 'how much is X?')\n"
        "- COMPARISON: comparing products or asking for recommendations between options\n"
        "- AVAILABILITY: asking about stock, delivery, or availability\n"
        "- COMPLAINT: expressing dissatisfaction, anger, or reporting a problem/defect\n"
        "- NEGOTIATION: asking for better price, bargaining, requesting refund\n"
        "- ORDER_PLACEMENT: user explicitly confirms purchase of a SPECIFIC named product "
        "(e.g. 'I want to order the Vivobook Pro', 'đặt sản phẩm đó', 'mua cái này'). "
        "NOT for browsing by price range or asking 'do you have X under Y price?'\n"
        "- SMALLTALK: greetings (xin chào, hi), general chitchat unrelated to products or sales. "
        "NOT for product browsing or catalog queries — those are INFO_QUERY.\n"
        "- FOLLOW_UP: asking about order status, checking progress of a previous order/request, "
        "or short status inquiries (e.g. 'đặt chưa?', 'đã đặt chưa?', 'rồi sao'). "
        "NOT for confirming a new purchase.\n"
        "- CANCEL: user explicitly requests to cancel an order, stop a purchase, or change their mind "
        "(e.g. 'hủy đơn', 'không mua nữa', 'hủy giúp tôi', 'tôi muốn hủy', 'cancel order').\n\n"
        "CRITICAL: 'I want laptops under 25 million' = PRICING. "
        "'Order THIS specific product' = ORDER_PLACEMENT. "
        "Asking 'Did you place it?' / 'đặt chưa?' = FOLLOW_UP. "
        "Asking to cancel an order = CANCEL. "
        "Respond ONLY with valid JSON matching the schema. "
        "Set primary_intent to the best matching intent. "
        "Set confidence 0.0-1.0. Keep reasoning concise."
    )

    # v3-0 P1 (T03 option 1): history-aware classification — same single call,
    # last 3 exchanges + previous intent as context, current message LAST.
    classify_input = user_msg
    if v3_enabled:
        context_parts = []
        if previous_intent:
            context_parts.append(f"Previous turn intent: {previous_intent}")
        history_block = _format_history(state.get("messages") or [], user_msg)
        if history_block:
            context_parts.append(f"Recent conversation (context only):\n{history_block}")
        if context_parts:
            system_prompt += (
                "\nClassify ONLY the LAST customer message; the conversation "
                "history is context, not the thing to classify. "
                "Set intent_shift=true if the last message changes intent "
                "vs the previous turn intent."
            )
            classify_input = (
                "\n\n".join(context_parts) + f"\n\nClassify the LAST customer message:\n{user_msg}"
            )
    try:
        try:
            result = await AIGateway.complete(
                model="economy-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": classify_input},
                ],
                response_format=IntentClassification,
            )
            raw_content = result.choices[0].message.content
        except Exception:
            result = await AIGateway.complete(
                model="economy-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": classify_input},
                ],
                response_format={"type": "json_object"},
            )
            raw_content = result.choices[0].message.content

        # Flexible parsing for primary_intent & secondary_intents
        import json

        data = json.loads(raw_content)
        primary = data.get("primary_intent") or data.get("intent") or "INFO_QUERY"
        try:
            primary_enum = IntentEnum(primary)
        except ValueError:
            primary_enum = IntentEnum.INFO_QUERY

        valid_secondaries = []
        for sec in data.get("secondary_intents") or []:
            try:
                valid_secondaries.append(IntentEnum(sec))
            except ValueError:
                pass

        classification = IntentClassification(
            primary_intent=primary_enum,
            secondary_intents=valid_secondaries,
            confidence=float(data.get("confidence", 0.9)),
            reasoning=str(data.get("reasoning", "")),
            intent_shift=bool(
                data.get("intent_shift", previous_intent not in (None, primary_enum.value))
            ),
        )
    except Exception as e:
        logger.warning(
            "router_node classification failed, falling back to INFO_QUERY: %s",
            e,
        )
        classification = _FALLBACK_CLASSIFICATION

    disagreement_count = int(state.get("intent_disagreement_count") or 0)
    if v3_enabled:
        # 1.2 (T06, H6): hard priority CANCEL > COMPLAINT > NEGOTIATION > ORDER > INFO.
        classification = normalize_priority(classification)
        # 1.1 (T03): sticky-intent transition table + confidence hysteresis.
        classification, disagreement_count = apply_transition(
            previous_intent, classification, disagreement_count
        )

    # FR-007: escalate if ANY intent (primary OR secondary) is COMPLAINT/NEGOTIATION
    next_node = _get_next_node(classification)
    update: dict = {
        "intent": classification.primary_intent.value,
        "secondary_intents": [i.value for i in classification.secondary_intents],
        "intent_confidence": classification.confidence,
        "intent_shift": classification.intent_shift,
        "intent_disagreement_count": disagreement_count,
    }

    # H6 (T06): mixed info+order in one message — handle the highest-priority
    # branch (ORDER) and queue the advisory part so order_execution answers it
    # with the confirmation (SC5 machinery). secondary_intents stay in state
    # for the next turn (make_initial_state no longer wipes them).
    if (
        v3_enabled
        and classification.primary_intent == IntentEnum.ORDER_PLACEMENT
        and _ADVISORY_SECONDARIES & set(classification.secondary_intents)
        and not state.get("pending_info_questions")
    ):
        update["pending_info_questions"] = user_msg

    return Command(goto=next_node, update=update)


def _get_next_node(classification: IntentClassification) -> str:
    """Routing map from intent to next node (T045)."""
    if classification.primary_intent == IntentEnum.CANCEL:
        return "cancellation_node"

    if classification.primary_intent == IntentEnum.ORDER_PLACEMENT:
        return "retrieval_node"

    # For all other intents: escalate if primary OR secondary is COMPLAINT/NEGOTIATION.
    if classification.has_escalation_intent():
        return "escalation_node"
    if classification.primary_intent == IntentEnum.SMALLTALK:
        return "answer_node"
    if classification.primary_intent == IntentEnum.FOLLOW_UP:
        return "memory_retrieval_node"
    # INFO_QUERY, PRICING, COMPARISON, AVAILABILITY, unknown
    return "retrieval_node"


def _route_after_router(state: AgentState) -> str:
    """Conditional edge after router_node (T045b, T051).

    Routes based on state intent that was set by router_node.
    This function is used for Mermaid diagram rendering to show the
    conditional paths from router_node, while the actual routing execution
    uses Command(goto=...) returned by router_node.

    Returns:
        One of: "retrieval_node", "memory_retrieval_node", "escalation_node", "answer_node"
    """
    intent_str = state.get("intent", "unknown")
    secondary_intents = state.get("secondary_intents", [])

    # FR-007: escalate if ANY intent (primary OR secondary) is COMPLAINT/NEGOTIATION
    if intent_str in ("COMPLAINT", "NEGOTIATION"):
        return "escalation_node"
    if any(si in ("COMPLAINT", "NEGOTIATION") for si in secondary_intents):
        return "escalation_node"

    # SMALLTALK primary intent → answer_node (no retrieval needed, save cost)
    if intent_str == "SMALLTALK":
        return "answer_node"
    if intent_str == "FOLLOW_UP":
        return "memory_retrieval_node"

    # ORDER_PLACEMENT, INFO_QUERY, PRICING, COMPARISON, AVAILABILITY → retrieval_node
    return "retrieval_node"
