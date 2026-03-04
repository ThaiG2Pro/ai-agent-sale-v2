"""Answer node for LangGraph sales agent (T048-T049).

Why: Universal trace point — all graph paths (accepted AND declined) route here
to ensure tracing happens (FR-008).

What: If declined, returns DECLINE_MESSAGE without LLM call.
Otherwise, builds prompt with citations context and calls LLM for response.
Writes model trace at end regardless of outcome.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from sqlalchemy import insert

from models.schema import ModelTrace
from services.ai import AIGateway
from services.rag.constants import DECLINE_MESSAGE

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.agent.state import AgentState


async def answer_node(state: AgentState, config: RunnableConfig) -> dict:
    """Generate final answer or decline message (T048).

    Universal trace point: writes model_traces regardless of accept/decline (FR-008).
    DB session injected via config["configurable"]["db"].

    Returns:
        State update dict with response, model_used
    """
    db = (config.get("configurable") or {}).get("db")

    # Path 1: Declined (Layer 1 or Layer 2 guard) → return without LLM
    if state.get("declined", False):
        await _write_model_trace(
            state,
            db=db,
            metadata_={
                "guard_decision": "REJECTED",
                "escalation_reason": state.get("escalation_reason"),
                "escalation_failure": state.get("escalation_failure", False),
                "escalation_flag": state.get("escalation_flag", False),
                "intended_model": state.get("model_used"),
            },
        )
        return {
            "response": DECLINE_MESSAGE,
            "model_used": None,
        }

    # Path 2: Accepted → call LLM with citations context
    model = state.get("model_used") or "economy-chat"

    # Build context from retrieved chunks (use all chunks, not just first)
    chunks = state.get("retrieved_chunks", [])
    chunk_text = "\n\n".join(c.get("text", "") for c in chunks if c.get("text"))

    citations_text = ""
    if state.get("citations"):
        citations_text = "\n\nNguồn tham khảo:\n"
        for i, citation in enumerate(state["citations"], 1):
            citations_text += f"{i}. {citation.name} ({citation.sku})\n"

    system_prompt = (
        "Bạn là trợ lý bán hàng AI chuyên nghiệp. "
        "Trả lời bằng tiếng Việt, thân thiện và hữu ích. "
        "Chỉ dùng thông tin từ context được cung cấp. "
        "Nếu không có thông tin phù hợp, nói rõ điều đó."
    )

    prompt = f"Context sản phẩm:\n{chunk_text}\n{citations_text}\nCâu hỏi: {state['user_message']}"

    try:
        result = await AIGateway.complete(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
        )
        response = result.choices[0].message.content
    except Exception as e:
        response = f"Lỗi khi tạo phản hồi: {e!s}"
        model = None

    # Universal trace write (FR-008)
    metadata_ = {
        "guard_decision": "ACCEPTED",
        "escalation_reason": state.get("escalation_reason"),
        "escalation_failure": state.get("escalation_failure", False),
        "escalation_flag": state.get("escalation_flag", False),
        "declined": False,
        "intended_model": model,
    }
    await _write_model_trace(state, db=db, metadata_=metadata_)

    return {
        "response": response,
        "model_used": model,
    }


async def _write_model_trace(
    state: AgentState, db: AsyncSession | None = None, metadata_: dict | None = None
) -> None:
    """Write model trace to agent_v1.model_traces table (T049).

    Called at end of answer_node for both accepted AND declined paths.
    Fail-safe: logs to stderr on error, doesn't block response.
    """
    if not db or not metadata_:
        return

    try:
        message_id = state.get("message_id")
        stmt = insert(ModelTrace).values(
            message_id=message_id,
            model_name=metadata_.get("intended_model") or "declined",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            latency_ms=None,
            cost=0.00,
            metadata_=metadata_,
        )
        await db.execute(stmt)
        await db.commit()
    except Exception as e:
        print(
            f"[TRACE_FAIL] session_id={state.get('session_id')}, error={e}",
            file=sys.stderr,
        )
