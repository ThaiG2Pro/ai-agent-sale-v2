"""Router node for LangGraph sales agent (T044-T045).

Why: First node in the agent graph — classifies user intent using LLM
and routes to next node based on intent category.

What: Uses AIGateway.complete (via ai_router) with response_format=IntentClassification
to classify multi-intent and route to retrieval_node, escalation_node, or
answer_node.
"""

from __future__ import annotations

from langgraph.types import Command

from core.agent.state import AgentState, IntentClassification, IntentEnum
from services.ai import AIGateway


async def router_node(state: AgentState) -> Command:
    """Classify user intent and route to next node (T044).

    Uses economy-chat model (not light-chat due to Ollama G1 constraint).
    Returns Command with goto and state updates.
    """
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
        "NOT for product browsing or catalog queries — those are INFO_QUERY.\n\n"
        "CRITICAL: 'I want laptops under 25 million' = PRICING. "
        "'Order THIS specific product' = ORDER_PLACEMENT. "
        "Respond ONLY with valid JSON matching the schema. "
        "Set primary_intent to the best matching intent. "
        "Set confidence 0.0-1.0. Keep reasoning concise."
    )
    result = await AIGateway.complete(
        model="economy-chat",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": state["user_message"]},
        ],
        response_format=IntentClassification,
    )
    classification = IntentClassification.model_validate_json(result.choices[0].message.content)
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
    """Routing map from intent to next node (T045).

    Routing logic (FR-007):
    - ANY intent is COMPLAINT or NEGOTIATION → escalation_node (intent-first escalation)
    - SMALLTALK primary intent → answer_node (no retrieval needed, save cost)
    - ORDER_PLACEMENT → retrieval_node (need context for the order)
    - INFO_QUERY, PRICING, COMPARISON, AVAILABILITY → retrieval_node (need context)
    - Unknown → retrieval_node (safe fallback)
    """
    # Check primary AND secondary intents per FR-007
    if classification.has_escalation_intent():
        return "escalation_node"
    if classification.primary_intent == IntentEnum.SMALLTALK:
        return "answer_node"
    # INFO_QUERY, PRICING, COMPARISON, AVAILABILITY, ORDER_PLACEMENT, unknown
    return "retrieval_node"


def _route_after_router(state: AgentState) -> str:
    """Conditional edge after router_node (T045b, T051).

    Routes based on state intent that was set by router_node.
    This function is used for Mermaid diagram rendering to show the
    conditional paths from router_node, while the actual routing execution
    uses Command(goto=...) returned by router_node.

    Returns:
        One of: "retrieval_node", "escalation_node", "answer_node"
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

    # ORDER_PLACEMENT, INFO_QUERY, PRICING, COMPARISON, AVAILABILITY → retrieval_node
    return "retrieval_node"
