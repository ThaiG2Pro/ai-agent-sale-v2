"""Escalation node for LangGraph sales agent (Phase 5, T063+).

Why: Decides whether to escalate from economy model to premium model
based on intent (COMPLAINT/NEGOTIATION) or borderline confidence.

What: Pure Python node (zero LLM call). Returns escalation decision
with flag, reason, and selected model for answer_node.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.agent.state import EscalationReasonEnum, IntentEnum
from core.config import settings

if TYPE_CHECKING:
    from core.agent.state import AgentState

logger = logging.getLogger(__name__)

_ESCALATION_INTENTS = {IntentEnum.COMPLAINT, IntentEnum.NEGOTIATION}


async def escalation_node(state: AgentState) -> dict:
    """Escalation decision node (T063-T064).

    Pure Python — zero LLM call. Intent-first logic:
    - COMPLAINT / NEGOTIATION (primary or secondary) → premium model.
    - INFO_QUERY routed here (borderline confidence from confidence_node) → premium.
    - Anything else → no escalation, economy-chat retained.
    - T064: if premium model is unavailable, fall back to economy-chat and
    set escalation_failure=True.

    Args:
        state: Current agent state

    Returns:
        State update dict: escalation_flag,
        escalation_reason, model_used, escalation_failure
    """
    intent = state.get("intent")
    secondary = set(state.get("secondary_intents") or [])

    # Determine if escalation is needed (intent-first, T063)
    should_escalate = False
    reason = EscalationReasonEnum.NONE

    # Intent-based escalation: COMPLAINT or NEGOTIATION in primary or secondary
    if intent in _ESCALATION_INTENTS or bool(secondary & _ESCALATION_INTENTS):
        should_escalate = True
        reason = EscalationReasonEnum.INTENT_ESCALATION

    # Score-based escalation: INFO_QUERY borderline (routed here by confidence_node)
    elif intent == IntentEnum.INFO_QUERY:
        should_escalate = True
        reason = EscalationReasonEnum.LOW_CONFIDENCE

    if not should_escalate:
        return {
            "escalation_flag": False,
            "escalation_reason": EscalationReasonEnum.NONE,
            "model_used": None,
            "escalation_failure": False,
        }

    # T064: Attempt to use premium model with graceful fallback
    selected_model = settings.PREMIUM_MODEL
    escalation_failure = False
    try:
        # Validate the model alias is configured (non-empty string check)
        if not selected_model:
            raise ValueError("PREMIUM_MODEL not configured")
        # Model is available — use it
    except Exception as e:
        logger.warning("escalation_failure reason=%s fallback=economy-chat", str(e))
        selected_model = "economy-chat"
        escalation_failure = True

    return {
        "escalation_flag": True,
        "escalation_reason": reason,
        "model_used": selected_model,
        "escalation_failure": escalation_failure,
    }
