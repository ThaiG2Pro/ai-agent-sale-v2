"""Clarify node for LangGraph sales agent (WP-V2-3).

Why: A borderline query (passed L1 similarity but low fused confidence) usually
means the customer was vague, not that the catalog lacks the answer. Asking ONE
clarifying question converts a decline into a second-turn answer.

What: Generates one short Vietnamese clarifying question with the economy model
(Pydantic ClarifyingQuestion), using the top retrieved product names as the
disambiguation candidates. Sets awaiting_clarification + stores the original
query so retrieval_node can merge the customer's reply on the next turn.
Anti-loop: clarify_count is incremented here; confidence_node refuses a second
clarify for the same original query (declines as before).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.agent.state import ClarifyingQuestion
from services.ai import AIGateway

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

    from core.agent.state import AgentState

logger = logging.getLogger(__name__)

# Static fallback when the LLM call fails — still better than a decline.
FALLBACK_CLARIFY_QUESTION = (
    "Dạ, anh/chị có thể mô tả rõ hơn sản phẩm hoặc thông tin đang cần được không ạ? "
    "Ví dụ tên sản phẩm cụ thể hoặc nhu cầu sử dụng."
)


def _candidate_names(state: AgentState, limit: int = 3) -> list[str]:
    """Top retrieved product names — the [X] hay [Y] candidates for the question."""
    names: list[str] = []
    for c in state.get("citations") or []:
        name = c.get("name") if isinstance(c, dict) else getattr(c, "name", None)
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


async def clarify_node(state: AgentState, config: RunnableConfig) -> dict:
    """Generate ONE clarifying question for a borderline query (WP-V2-3).

    Routed from confidence_node when needs_clarification is set. The question
    becomes this turn's response (answer_node Path 0 traces it); the next turn
    retrieval_node merges the customer's reply into clarify_original_query.

    Returns:
        State update dict with response, awaiting_clarification, original query
        storage, and the incremented clarify counter.
    """
    user_message = state["user_message"]
    candidates = _candidate_names(state)

    question = FALLBACK_CLARIFY_QUESTION
    try:
        candidate_note = (
            f"Các sản phẩm gần đúng nhất trong catalog: {', '.join(candidates)}. "
            if candidates
            else ""
        )
        system_prompt = (
            "Bạn là trợ lý bán hàng AI tiếng Việt. Câu hỏi của khách chưa đủ rõ để "
            "trả lời chắc chắn. Hãy đặt ĐÚNG MỘT câu hỏi làm rõ, ngắn gọn, lịch sự "
            "(xưng 'em', gọi khách 'anh/chị'). "
            f"{candidate_note}"
            "Nếu có các sản phẩm gần đúng, hỏi dạng 'Anh/chị đang hỏi về [X] hay [Y] ạ?'. "
            "Không trả lời câu hỏi gốc, không xin lỗi dài dòng. "
            "Respond ONLY with valid JSON matching the schema."
        )
        result = await AIGateway.complete(
            model="economy-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            response_format=ClarifyingQuestion,
        )
        question = ClarifyingQuestion.model_validate_json(
            result.choices[0].message.content
        ).question
    except Exception as e:
        logger.warning("clarify_node LLM call failed, using static fallback: %s", e)

    logger.info("Clarify question for %r → %r", user_message, question)
    return {
        "response": question,
        "model_used": "clarify",
        "declined": False,
        "awaiting_clarification": True,
        "clarify_original_query": user_message,
        "clarify_count": int(state.get("clarify_count") or 0) + 1,
    }
