"""Answer node for LangGraph sales agent (T048-T049).

Why: Universal trace point — all graph paths (accepted AND declined) route here
to ensure tracing happens (FR-008).

What: If declined, returns DECLINE_MESSAGE without LLM call.
Otherwise, builds prompt with citations context and calls litellm for response.
Writes model trace at end regardless of outcome.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import litellm
from sqlalchemy import insert

from models.schema import ModelTrace
from services.rag.constants import DECLINE_MESSAGE

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.agent.state import AgentState


async def answer_node(state: AgentState, db: AsyncSession | None = None) -> dict:
    """Generate final answer or decline message (T048).

    Universal trace point: writes model_traces regardless of accept/decline (FR-008).

    Args:
        state: Current agent state
        db: AsyncSession for database writes (injected by graph)

    Returns:
        State update dict with response, model_used
    """
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
    citations_text = ""
    if state.get("citations"):
        citations_text = "\n\nSources:\n"
        for i, citation in enumerate(state["citations"], 1):
            citations_text += f"{i}. {citation.name} ({citation.sku})\n"

    # Build prompt with retrieved chunks and citations
    chunks = state.get("retrieved_chunks", [{}])
    chunk_text = chunks[0].get("text", "") if chunks else ""

    prompt = f"""Based on the following product information, answer the user's question.

{chunk_text}
{citations_text}

User question: {state["user_message"]}"""

    try:
        result = await litellm.acompletion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        response = result.choices[0].message.content
    except Exception as e:
        response = f"Error generating response: {e!s}"
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

    Args:
        state: Current agent state
        db: AsyncSession for database writes
        metadata_: JSONB metadata dict
    """
    if not db or not metadata_:
        return

    try:
        # Extract message_id if conversation message exists
        message_id = state.get("message_id")

        # Build insert statement per FR-008
        stmt = insert(ModelTrace).values(
            message_id=message_id,
            model_name=metadata_.get("intended_model", "unknown"),
            prompt_tokens=0,  # TODO(Phase 5): Track from LLM response
            completion_tokens=0,  # TODO(Phase 5): Track from LLM response
            total_tokens=0,  # TODO(Phase 5): Track from LLM response
            latency_ms=None,  # TODO(Phase 5): Track wall-clock time
            cost=0.00,  # TODO(Phase 5): Calculate from token counts
            metadata_=metadata_,
        )

        await db.execute(stmt)
        await db.commit()
    except Exception as e:
        # FR-008 requirement: Fail-safe logging without blocking response
        print(
            f"[TRACE_FAIL] session_id={state.get('session_id')}, error={e}",
            file=sys.stderr,
        )
