"""support_router_node — intent classification for the Spacely support graph.

Why this exists: the shop router (core/agent/nodes/router.py) is a sales state
machine (cancel keywords, hesitation flips, sticky transitions, order/HITL
routing). A support desk needs four intents and two destinations. This node
keeps the two zero-LLM fast paths that are domain-neutral and otherwise does
one light-tier structured call.

Routing:
  SMALLTALK                         → support_answer_node
  INFO_QUERY / PRICING / COMPLAINT  → retrieval_node (shared with the shop graph)
"""

from __future__ import annotations

import logging

from langgraph.types import Command

from core.agent.nodes.router import _format_history, _smalltalk_fastpath
from core.agent.state import AgentState, IntentClassification, IntentEnum
from core.support.persona import SPACELY_SUPPORT
from services.ai import AIGateway

logger = logging.getLogger(__name__)

SUPPORT_INTENTS: frozenset[IntentEnum] = frozenset(
    {IntentEnum.INFO_QUERY, IntentEnum.PRICING, IntentEnum.COMPLAINT, IntentEnum.SMALLTALK}
)
# Light tier: a 4-way label on a short message; the answer step pays for reasoning.
_ROUTER_MODEL = "light-chat"

_FALLBACK = IntentClassification(
    primary_intent=IntentEnum.INFO_QUERY,
    secondary_intents=[],
    confidence=0.0,
    reasoning="fallback: classification failed",
)


def normalize_support_intent(raw: str | None) -> IntentEnum:
    """Fold anything outside the four support intents into INFO_QUERY.

    The LLM may still emit shop labels (ORDER_PLACEMENT, NEGOTIATION, …) or
    free-form synonyms; retrieval + confidence gating handles those safely,
    whereas routing them into order flows would not.
    """
    value = (raw or "").strip().upper()
    if value in ("GREETING", "CHITCHAT", "HELLO", "THANKS"):
        return IntentEnum.SMALLTALK
    if value in ("PRICE", "COST", "CREDIT", "BILLING"):
        return IntentEnum.PRICING
    if value in ("BUG", "ISSUE", "REFUND", "PROBLEM"):
        return IntentEnum.COMPLAINT
    try:
        intent = IntentEnum(value)
    except ValueError:
        return IntentEnum.INFO_QUERY
    return intent if intent in SUPPORT_INTENTS else IntentEnum.INFO_QUERY


def _next_node(intent: IntentEnum) -> str:
    return "support_answer_node" if intent == IntentEnum.SMALLTALK else "retrieval_node"


async def classify_support_intent(user_msg: str, messages: list) -> IntentClassification:
    """One structured light-tier call; never raises (falls back to INFO_QUERY)."""
    history = _format_history(messages or [], user_msg)
    classify_input = user_msg
    system_prompt = SPACELY_SUPPORT.router_system_prompt
    if history:
        system_prompt += (
            "\nClassify ONLY the LAST user message; the conversation history is "
            "context, not the thing to classify."
        )
        classify_input = (
            f"Recent conversation (context only):\n{history}\n\n"
            f"Classify the LAST user message:\n{user_msg}"
        )
    try:
        data = await AIGateway.complete_json(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": classify_input},
            ],
            model=_ROUTER_MODEL,
            schema=IntentClassification,
        )
    except Exception as exc:
        logger.warning("support_router classification failed, INFO_QUERY fallback: %s", exc)
        return _FALLBACK

    primary = normalize_support_intent(str(data.get("primary_intent") or data.get("intent") or ""))
    try:
        confidence = float(data.get("confidence", 0.9))
    except (TypeError, ValueError):
        confidence = 0.9
    return IntentClassification(
        primary_intent=primary,
        secondary_intents=[],
        confidence=max(0.0, min(1.0, confidence)),
        reasoning=str(data.get("reasoning", ""))[:200],
    )


async def support_router_node(state: AgentState) -> Command:
    user_msg = state.get("user_message") or ""
    previous_intent = state.get("intent")

    if _smalltalk_fastpath(user_msg):
        return Command(
            goto="support_answer_node",
            update={
                "intent": IntentEnum.SMALLTALK.value,
                "secondary_intents": [],
                "intent_confidence": 1.0,
                "intent_shift": previous_intent not in (None, IntentEnum.SMALLTALK.value),
                "smalltalk_fastpath": True,
            },
        )

    classification = await classify_support_intent(user_msg, state.get("messages") or [])
    intent = classification.primary_intent
    logger.info(
        "support_router: %r → %s (%.2f)", user_msg[:60], intent.value, classification.confidence
    )
    return Command(
        goto=_next_node(intent),
        update={
            "intent": intent.value,
            "secondary_intents": [],
            "intent_confidence": classification.confidence,
            "intent_shift": previous_intent not in (None, intent.value),
            "smalltalk_fastpath": False,
        },
    )
