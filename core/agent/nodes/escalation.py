"""Escalation node for LangGraph sales agent (Phase 5, T063+).

Why: Decides whether to escalate from economy model to premium model
based on intent (COMPLAINT/NEGOTIATION) or borderline confidence.

What: Pure Python node (zero LLM call). Returns escalation decision
with flag, reason, and selected model for answer_node.
"""

from __future__ import annotations

from core.agent.state import AgentState, EscalationReasonEnum


async def escalation_node(state: AgentState) -> dict:
    """Escalation decision node (Phase 5 stub).

    For Phase 4, this is a placeholder that always returns no escalation.
    Phase 5 will implement intent-based and score-based escalation logic.

    Args:
        state: Current agent state

    Returns:
        State update dict with escalation_flag, escalation_reason, model_used
    """
    # Phase 4 stub: no escalation logic yet
    return {
        "escalation_flag": False,
        "escalation_reason": EscalationReasonEnum.NONE,
        "model_used": None,
    }
