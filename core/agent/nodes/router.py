"""Router node for LangGraph sales agent (T044-T045).

Why: First node in the agent graph — classifies user intent using LLM
and routes to next node based on intent category.

What: Uses AIGateway.complete (via ai_router) with response_format=IntentClassification
to classify multi-intent and route to retrieval_node, escalation_node, or
answer_node.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.types import Command

from core.agent.state import IntentClassification, IntentEnum
from services.ai import AIGateway

if TYPE_CHECKING:
    from core.agent.state import AgentState


async def router_node(state: AgentState) -> Command:
    """Classify user intent and route to next node (T044).

    Uses economy-chat model (not light-chat due to Ollama G1 constraint).
    Returns Command with goto and state updates.
    """
    system_prompt = (
        "You are an intent classifier for a Vietnamese e-commerce sales agent. "
        "Classify the user message into one of these intents:\n"
        "- INFO_QUERY: asking about product features, specs, or general info\n"
        "- PRICING: asking about price, cost, discount, or promotion\n"
        "- COMPARISON: comparing products or asking for recommendations\n"
        "- AVAILABILITY: asking about stock, delivery, or availability\n"
        "- COMPLAINT: expressing dissatisfaction, anger, or reporting a problem/defect\n"
        "- NEGOTIATION: asking for better price, bargaining, requesting refund\n"
        "- SMALLTALK: greetings, chitchat, or unrelated conversation\n\n"
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
    - INFO_QUERY, PRICING, COMPARISON, AVAILABILITY → retrieval_node (need context)
    - Unknown → retrieval_node (safe fallback)
    """
    # Check primary AND secondary intents per FR-007
    if classification.has_escalation_intent():
        return "escalation_node"
    if classification.primary_intent == IntentEnum.SMALLTALK:
        return "answer_node"
    # INFO_QUERY, PRICING, COMPARISON, AVAILABILITY, unknown
    return "retrieval_node"
