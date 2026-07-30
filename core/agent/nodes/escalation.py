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
    - INFO_QUERY borderline (confidence 0.45-0.70) → premium model.
    - PRICING / AVAILABILITY borderline → economy model (answer with retrieved chunks).
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

    # Score-based escalation:
    # - INFO_QUERY borderline → premium model (complex borderline info query)
    # - PRICING / AVAILABILITY borderline → economy model (answer with retrieved chunks)
    elif intent == IntentEnum.INFO_QUERY:
        should_escalate = True
        reason = EscalationReasonEnum.LOW_CONFIDENCE
    elif intent in (IntentEnum.PRICING, IntentEnum.AVAILABILITY):
        # Borderline confidence but chunks found — answer with economy model (no escalation)
        return {
            "escalation_flag": False,
            "escalation_reason": EscalationReasonEnum.NONE,
            "model_used": "economy-chat",
            "escalation_failure": False,
        }

    if not should_escalate:
        return {
            "escalation_flag": False,
            "escalation_reason": EscalationReasonEnum.NONE,
            "model_used": None,
            "escalation_failure": False,
        }

    # T064: config-level check only — runtime availability can't be known here
    # without an LLM call (this node is zero-LLM by design). The REAL fallback
    # happens at point of use: answer_node degrades premium → economy-chat and
    # sets escalation_failure=True when the premium call actually fails.
    selected_model = settings.PREMIUM_MODEL
    escalation_failure = False
    if not selected_model:
        logger.warning(
            "escalation_failure reason=PREMIUM_MODEL not configured fallback=economy-chat"
        )
        selected_model = "economy-chat"
        escalation_failure = True

    return {
        "escalation_flag": True,
        "escalation_reason": reason,
        "model_used": selected_model,
        "escalation_failure": escalation_failure,
    }
