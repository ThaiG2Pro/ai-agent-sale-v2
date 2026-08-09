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

from core.agent.state import AgentState, IntentClassification, IntentEnum
from services.ai import AIGateway

logger = logging.getLogger(__name__)

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
    user_msg_lower = (state.get("user_message") or "").lower()
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
    try:
        try:
            result = await AIGateway.complete(
                model="economy-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": state["user_message"]},
                ],
                response_format=IntentClassification,
            )
            raw_content = result.choices[0].message.content
        except Exception:
            result = await AIGateway.complete(
                model="economy-chat",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": state["user_message"]},
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
        )
    except Exception as e:
        logger.warning(
            "router_node classification failed, falling back to INFO_QUERY: %s",
            e,
        )
        classification = _FALLBACK_CLASSIFICATION
    # FR-007: escalate if ANY intent (primary OR secondary) is COMPLAINT/NEGOTIATION
    next_node = _get_next_node(classification)
    return Command(
        goto=next_node,
        update={
            "intent": classification.primary_intent.value,
            "secondary_intents": [i.value for i in classification.secondary_intents],
            "intent_confidence": classification.confidence,
        },
    )


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
