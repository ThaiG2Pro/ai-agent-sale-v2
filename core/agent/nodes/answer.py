"""Answer node for LangGraph sales agent (T048-T049).

Why: Universal trace point — all graph paths (accepted AND declined) route here
to ensure tracing happens (FR-008).

What:
- Cache hit path: returns cached_answer directly (no LLM call)
- Declined path: returns DECLINE_MESSAGE (no LLM call)
- Accepted path: builds context from retrieved_chunks, calls LLM, writes cache

Cache write happens here (not in retrieval_node) because we only write
the final answer after the correct model (economy or premium) has generated it.
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

    Paths:
    0. Already responded (response set by business node) → return as-is (tracing only)
    1. Cache hit (cached_answer set) → return cached answer, no LLM call
    2. Declined (Layer 1 or Layer 2) → return DECLINE_MESSAGE, no LLM call
    3. Accepted → LLM call with retrieved_chunks context, then write to cache

    Returns:
        State update dict with response, model_used
    """
    db = (config.get("configurable") or {}).get("db")

    # Path 0: Already responded by a business node (e.g., order_execution or customer_support)
    # We still want to write a trace for this turn.
    if state.get("response"):
        await _write_model_trace(
            state,
            db=db,
            metadata_={
                "guard_decision": "BUSINESS_LOGIC",
                "escalation_flag": state.get("escalation_flag", False),
                "declined": False,
                "intended_model": "business_logic",
            },
        )
        return {}  # No additional updates needed

    # Path 1: Cache hit — use pre-generated answer, skip LLM entirely
    cached_answer = state.get("cached_answer")
    if cached_answer and not state.get("declined", False):
        await _write_model_trace(
            state,
            db=db,
            metadata_={
                "guard_decision": "CACHE_HIT",
                "escalation_reason": state.get("escalation_reason"),
                "escalation_failure": state.get("escalation_failure", False),
                "escalation_flag": state.get("escalation_flag", False),
                "declined": False,
                "intended_model": "cache",
            },
        )
        return {
            "response": cached_answer,
            "model_used": "cache",
        }

    # Path 2: Declined (Layer 1 or Layer 2 guard) → return without LLM
    if state.get("declined", False):
        # SC01 fix: vague browse INFO_QUERY → show product catalog instead of DECLINE_MESSAGE
        if state.get("intent") == "INFO_QUERY" and db:
            catalog_response = await _generate_catalog_response(state, db)
            if catalog_response:
                await _write_model_trace(
                    state,
                    db=db,
                    metadata_={
                        "guard_decision": "CATALOG_FALLBACK",
                        "escalation_flag": False,
                        "declined": False,
                        "intended_model": "catalog_fallback",
                    },
                )
                return {"response": catalog_response, "model_used": "catalog_fallback"}

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

    # Path 3: Accepted → call LLM with citations context
    model = state.get("model_used") or "economy-chat"

    # Build context from retrieved chunks (use all chunks, not just first)
    chunks = state.get("retrieved_chunks", [])
    chunk_text = "\n\n".join(c.get("text", "") for c in chunks if c.get("text"))

    citations_text = ""
    if state.get("citations"):
        citations_text = "\n\nNguồn tham khảo:\n"
        for i, citation in enumerate(state["citations"], 1):
            citations_text += f"{i}. {citation.name} ({citation.sku})\n"

    # SC07 fix: SMALLTALK path — domain guardrail to prevent off-topic answers
    if state.get("intent") == "SMALLTALK":
        system_prompt = (
            "Bạn là trợ lý bán hàng AI chuyên về điện tử tiêu dùng. "
            "Nhiệm vụ DUY NHẤT của bạn là tư vấn sản phẩm điện tử, giá cả và hỗ trợ đặt hàng. "
            "Nếu khách hỏi về chủ đề NGOÀI phạm vi bán hàng điện tử "
            "(lập trình, nấu ăn, thời tiết, học thuật, v.v.): "
            "hãy lịch sự từ chối và mời khách tìm hiểu sản phẩm điện tử đang có. "
            "Nếu là lời chào: trả lời thân thiện và giới thiệu ngắn gọn về dịch vụ tư vấn."
        )
    else:
        system_prompt = (
            "Bạn là trợ lý bán hàng AI chuyên nghiệp. "
            "Trả lời bằng tiếng Việt, thân thiện và hữu ích. "
            "Chỉ dùng thông tin từ context được cung cấp. "
            "Nếu không có thông tin phù hợp, nói rõ điều đó."
        )

    if state.get("intent") == "SMALLTALK":
        prompt = state["user_message"]
    else:
        user_q = state["user_message"]
        prompt = f"Context sản phẩm:\n{chunk_text}\n{citations_text}\nCâu hỏi: {user_q}"

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

    # Write to cache after successful generation (best-effort)
    if response and db and state.get("canonical_query") and state.get("query_vector"):
        await _write_cache(state, response, db)

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


async def _generate_catalog_response(state: AgentState, db: AsyncSession) -> str | None:
    """SC01 fix: generate a product catalog response for vague browse queries.

    Called when INFO_QUERY is declined (no specific product match). Fetches
    all product names from DB and returns a formatted catalog listing.
    Only activates for very short / vague queries (≤ 6 words, no product keywords).

    Returns formatted catalog string or None (fall through to DECLINE_MESSAGE).
    """
    import re as _re

    query = state.get("user_message", "")
    words = query.split()

    # Specific product keywords that should NOT trigger catalog fallback
    _specific_keywords = _re.compile(
        r"\b(laptop|điện thoại|phone|tablet|máy tính|tai nghe|headphone|"
        r"keyboard|chuột|mouse|ssd|ram|gpu|card|màn hình|monitor|charger|"
        r"pin|sạc|macbook|iphone|samsung|xiaomi|asus|dell|lenovo|sony)\b",
        _re.IGNORECASE | _re.UNICODE,
    )
    if len(words) > 6 or _specific_keywords.search(query):
        return None

    try:
        from sqlalchemy import text as sql_text

        rows = await db.execute(
            sql_text("SELECT name, sku FROM agent_v1.products ORDER BY name LIMIT 12")
        )
        products = rows.fetchall()
        if not products:
            return None

        lines = ["**Sản phẩm hiện có tại shop:**\n"]
        for name, sku in products:
            lines.append(f"• {name} ({sku})")
        lines.append(
            "\nBạn quan tâm đến sản phẩm nào? Hãy hỏi thêm để biết thông tin chi tiết! 😊"
        )
        return "\n".join(lines)
    except Exception as exc:
        import sys

        print(f"[CATALOG_FALLBACK_FAIL] {exc}", file=sys.stderr)
        return None


async def _write_cache(state: AgentState, response: str, db: AsyncSession) -> None:
    """Write answer to semantic cache (L1+L2) after successful LLM generation."""
    try:
        from core.config import settings
        from services.semantic_cache import set_cache

        citations_for_cache = []
        for c in state.get("citations") or []:
            if hasattr(c, "model_dump"):
                citations_for_cache.append(c.model_dump())
            elif isinstance(c, dict):
                citations_for_cache.append(c)

        await set_cache(
            db=db,
            query=state["canonical_query"],
            response=response,
            embedding=state["query_vector"],
            model_name=settings.EMBED_MODEL,
            citations=citations_for_cache,
        )
    except Exception as exc:
        print(f"[CACHE_WRITE_FAIL] {exc}", file=sys.stderr)


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
