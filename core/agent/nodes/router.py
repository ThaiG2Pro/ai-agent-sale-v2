"""Router node for LangGraph sales agent (T044-T045).

Why: First node in the agent graph — classifies user intent using LLM
and routes to next node based on intent category.

What: Uses litellm.acompletion with response_format=IntentClassification
to classify multi-intent and route to retrieval_node, escalation_node, or
answer_node.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import litellm
from langgraph.types import Command

from core.agent.state import IntentClassification, IntentEnum

if TYPE_CHECKING:
    from core.agent.state import AgentState


async def router_node(state: AgentState) -> Command:
    """Classify user intent and route to next node (T044).

    Uses economy-chat model (not light-chat due to Ollama G1 constraint).
    Returns Command with goto and state updates.
    """
    result = await litellm.acompletion(
        model="economy-chat",
        messages=[{"role": "user", "content": state["user_message"]}],
        response_format=IntentClassification,
    )
    classification = IntentClassification.model_validate_json(
        result.choices[0].message.content
    )
    next_node = _get_next_node(classification.primary_intent)
    return Command(
        goto=next_node,
        update={
            "intent": classification.primary_intent.value,
            "secondary_intents": [i.value for i in classification.secondary_intents],
            "intent_confidence": classification.confidence,
        },
    )


def _get_next_node(primary_intent: IntentEnum) -> str:
    """Routing map from intent to next node (T045).

    Routing logic:
    - COMPLAINT, NEGOTIATION → escalation_node (intent-first escalation, FR-007)
    - SMALLTALK → answer_node (no retrieval needed, save cost)
    - INFO_QUERY, PRICING, COMPARISON, AVAILABILITY → retrieval_node (need context)
    - Unknown → retrieval_node (safe fallback)
    """
    escalation_intents = {IntentEnum.COMPLAINT, IntentEnum.NEGOTIATION}
    answer_intents = {IntentEnum.SMALLTALK}

    if primary_intent in escalation_intents:
        return "escalation_node"
    elif primary_intent in answer_intents:
        return "answer_node"
    else:
        # INFO_QUERY, PRICING, COMPARISON, AVAILABILITY, unknown
        return "retrieval_node"
