"""support_clarify_node — ONE clarifying question, support wording.

Same mechanics as core/agent/nodes/clarify.py (routed from confidence_node on
needs_clarification; sets awaiting_clarification so retrieval_node merges the
reply next turn; clarify_count feeds the anti-loop in confidence_node). Only
the prompt differs: the shop version talks about "sản phẩm trong catalog" and
addresses the customer as anh/chị.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.agent.nodes.clarify import _candidate_names
from core.agent.state import ClarifyingQuestion
from core.support.persona import SPACELY_SUPPORT
from services.ai import AIGateway

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

    from core.agent.state import AgentState

logger = logging.getLogger(__name__)

FALLBACK_CLARIFY_QUESTION = (
    "Bạn mô tả rõ hơn giúp mình được không? Ví dụ bạn đang hỏi về cách dùng, "
    "credit/giá hay tài khoản?"
)


async def support_clarify_node(state: AgentState, config: RunnableConfig) -> dict:
    user_message = state["user_message"]
    candidates = _candidate_names(state)
    candidate_note = f"Các chủ đề gần đúng nhất: {', '.join(candidates)}. " if candidates else ""

    question = FALLBACK_CLARIFY_QUESTION
    try:
        result = await AIGateway.complete_structured(
            ClarifyingQuestion,
            messages=[
                {
                    "role": "system",
                    "content": SPACELY_SUPPORT.clarify_system_prompt.replace(
                        "{candidate_note}", candidate_note
                    ),
                },
                {"role": "user", "content": user_message},
            ],
            model="economy-chat",
        )
        question = result.question
    except Exception as exc:
        logger.warning("support_clarify LLM call failed, static fallback: %s", exc)

    return {
        "response": question,
        "model_used": "clarify",
        "declined": False,
        "awaiting_clarification": True,
        "clarify_original_query": user_message,
        "clarify_count": int(state.get("clarify_count") or 0) + 1,
    }
