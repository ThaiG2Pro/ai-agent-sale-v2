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
import time
from typing import TYPE_CHECKING

from sqlalchemy import insert

from core.agent.state import EscalationReasonEnum
from core.config import settings
from models.schema import ModelTrace
from services.ai import AIGateway, extract_llm_metrics
from services.rag.constants import DECLINE_MESSAGE

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.agent.state import AgentState
    from services.ai import LLMUsageMetrics


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
        # P2 fix: surface rejection reason if admin rejected previous order
        rejection_note = ""
        if state.get("hitl_rejection_reason"):
            rejection_note = (
                f"\n[Lưu ý hệ thống]: Đơn hàng gần nhất của khách đã bị từ chối. "
                f"Lý do: {state['hitl_rejection_reason']}. "
                "Nếu khách hỏi về lý do từ chối, hãy giải thích rõ ràng và đề xuất hỗ trợ."
            )

        # T086: Add memory context from previous conversations if available
        memory_note = ""
        if state.get("memory_context") and len(state["memory_context"]) > 0:
            # T108: Context compression - replace old messages with summary + last 5 messages
            if state.get("thread_summary_exists"):
                # Use summary + last 5 messages for compression
                memory_context_text = _compress_context(state["memory_context"])
            else:
                # All messages (no compression)
                memory_context_text = "\n".join(
                    f"- {ctx.get('summary', ctx.get('text', ''))}"
                    for ctx in state["memory_context"]
                )
            memory_note = f"\n[Ngữ cảnh từ các cuộc hội thoại trước]:\n{memory_context_text}"

        system_prompt = (
            "Bạn là trợ lý bán hàng AI chuyên nghiệp. "
            "Trả lời bằng tiếng Việt, thân thiện và hữu ích. "
            "Chỉ dùng thông tin từ context được cung cấp. "
            f"Nếu không có thông tin phù hợp, nói rõ điều đó.{rejection_note}{memory_note}"
        )

    if state.get("intent") == "SMALLTALK":
        prompt = state["user_message"]
    else:
        user_q = state["user_message"]
        prompt = f"Context sản phẩm:\n{chunk_text}\n{citations_text}\nCâu hỏi: {user_q}"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    escalation_failure = state.get("escalation_failure", False)

    # ── WP-V2-1 cascade verification (research §7): intent escalations
    # (COMPLAINT/NEGOTIATION) answer on economy-chat first; PREMIUM_MODEL is
    # spent only when the groundedness verdict fails. Requires the groundedness
    # check as its verifier — with either switch off, premium goes direct (old
    # behavior). LOW_CONFIDENCE escalations keep premium direct: their trigger
    # already IS low confidence, so an economy first pass would just be wasted.
    cascade_target: str | None = None
    if (
        settings.CASCADE_VERIFY_ENABLED
        and settings.GROUNDEDNESS_CHECK_ENABLED
        and model != "economy-chat"
        and state.get("escalation_reason") == EscalationReasonEnum.INTENT_ESCALATION
        and state.get("intent") != "SMALLTALK"
    ):
        cascade_target = model
        model = "economy-chat"

    metrics: LLMUsageMetrics | None = None
    start_time = time.perf_counter()
    try:
        result = await AIGateway.complete(model=model, messages=messages)
        response = result.choices[0].message.content
        metrics = extract_llm_metrics(result, latency_ms=(time.perf_counter() - start_time) * 1000)
    except Exception as e:
        # T064 real fallback: premium failed at point of use → degrade to
        # economy-chat (escalation_failure=True). Cascade inverse: the economy
        # first pass failed → go straight to the reserved premium target.
        alt_model = cascade_target if model == "economy-chat" else "economy-chat"
        if alt_model:
            try:
                model = alt_model
                escalation_failure = alt_model == "economy-chat"
                result = await AIGateway.complete(model=model, messages=messages)
                response = result.choices[0].message.content
                metrics = extract_llm_metrics(
                    result, latency_ms=(time.perf_counter() - start_time) * 1000
                )
            except Exception as e2:
                response = f"Lỗi khi tạo phản hồi: {e2!s}"
                model = None
        else:
            response = f"Lỗi khi tạo phản hồi: {e!s}"
            model = None

    # ── WP-V2-1 groundedness self-check (kill switch: GROUNDEDNESS_CHECK_ENABLED).
    # Skipped for SMALLTALK (no retrieval context to ground against) and when
    # generation itself failed (metrics is None).
    grounded_declined = False
    groundedness_meta: dict | None = None
    if (
        metrics is not None
        and settings.GROUNDEDNESS_CHECK_ENABLED
        and state.get("intent") != "SMALLTALK"
        and chunk_text
    ):
        response, model, metrics, grounded_declined, groundedness_meta = await _verify_grounded(
            state=state,
            messages=messages,
            model=model,
            cascade_target=cascade_target,
            response=response,
            metrics=metrics,
            context=f"{chunk_text}\n{citations_text}",
            start_time=start_time,
        )
        if grounded_declined:
            response = DECLINE_MESSAGE

    # Write to cache after successful generation (best-effort) — never cache an
    # answer the groundedness verdict rejected.
    if (
        response
        and not grounded_declined
        and db
        and state.get("canonical_query")
        and state.get("query_vector")
    ):
        await _write_cache(state, response, db)

    # Universal trace write (FR-008)
    metadata_ = {
        "guard_decision": "GROUNDEDNESS_REJECTED" if grounded_declined else "ACCEPTED",
        "escalation_reason": state.get("escalation_reason"),
        "escalation_failure": escalation_failure,
        "escalation_flag": state.get("escalation_flag", False),
        "declined": grounded_declined,
        "intended_model": model,
        **(groundedness_meta or {}),
    }
    await _write_model_trace(state, db=db, metadata_=metadata_, metrics=metrics)

    return {
        "response": response,
        "model_used": model,
        "escalation_failure": escalation_failure,
        "declined": grounded_declined,
    }


async def _verify_grounded(
    state: AgentState,
    messages: list[dict[str, str]],
    model: str | None,
    cascade_target: str | None,
    response: str,
    metrics: LLMUsageMetrics | None,
    context: str,
    start_time: float,
) -> tuple[str, str | None, LLMUsageMetrics | None, bool, dict]:
    """WP-V2-1 verify → regenerate → decline loop for the graph answer path.

    - answerable=False → decline immediately (regen cannot conjure a product the
      catalog does not have).
    - supported=False → regenerate with STRICT_GROUNDING_SUFFIX and re-grade, up
      to GROUNDEDNESS_MAX_REGEN attempts. Under cascade the FIRST retry switches
      to the reserved premium target (that switch is the cascade escalation, so
      one retry is always budgeted); still unsupported → decline.

    Returns (response, model, metrics, declined, trace_metadata). Never raises.
    """
    from services.rag.groundedness import STRICT_GROUNDING_SUFFIX, check_groundedness

    verdict = await check_groundedness(state["user_message"], response, context)
    regen_count = 0
    cascade_escalated = False
    budget = settings.GROUNDEDNESS_MAX_REGEN
    if cascade_target is not None:
        budget = max(budget, 1)

    if verdict.answerable and not verdict.supported:
        strict_messages = [
            {"role": "system", "content": messages[0]["content"] + STRICT_GROUNDING_SUFFIX},
            *messages[1:],
        ]
        while regen_count < budget and not verdict.supported:
            regen_count += 1
            if cascade_target is not None and not cascade_escalated:
                model = cascade_target
                cascade_escalated = True
            try:
                result = await AIGateway.complete(model=model, messages=strict_messages)
                response = result.choices[0].message.content
                metrics = extract_llm_metrics(
                    result, latency_ms=(time.perf_counter() - start_time) * 1000
                )
            except Exception as exc:
                print(f"[GROUNDEDNESS_REGEN_FAIL] {exc}", file=sys.stderr)
                break
            verdict = await check_groundedness(state["user_message"], response, context)

    declined = not (verdict.answerable and verdict.supported)
    meta = {
        "groundedness": {
            "answerable": verdict.answerable,
            "supported": verdict.supported,
            "unsupported_claims": verdict.unsupported_claims[:5],
            "regen_count": regen_count,
            "cascade_escalated": cascade_escalated,
        }
    }
    return response, model, metrics, declined, meta


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
    state: AgentState,
    db: AsyncSession | None = None,
    metadata_: dict | None = None,
    metrics: LLMUsageMetrics | None = None,
) -> None:
    """Write model trace to agent_v1.model_traces table (T049).

    Called at end of answer_node for both accepted AND declined paths.
    `metrics` carries real token/cost/latency numbers from the LLM call;
    None (cache hit / declined / business path) writes zeros — correct,
    since no LLM call happened.
    Fail-safe: logs to stderr on error, doesn't block response.
    """
    if not db or not metadata_:
        return

    try:
        message_id = state.get("message_id")
        stmt = insert(ModelTrace).values(
            message_id=message_id,
            model_name=metadata_.get("intended_model") or "declined",
            prompt_tokens=metrics.prompt_tokens if metrics else 0,
            completion_tokens=metrics.completion_tokens if metrics else 0,
            total_tokens=metrics.total_tokens if metrics else 0,
            latency_ms=metrics.latency_ms if metrics else None,
            cost=metrics.cost if metrics else 0.00,
            metadata_=metadata_,
        )
        await db.execute(stmt)
        await db.commit()
    except Exception as e:
        print(
            f"[TRACE_FAIL] session_id={state.get('session_id')}, error={e}",
            file=sys.stderr,
        )


def _compress_context(memory_context: list[dict]) -> str:
    """Compress long memory context to summary + last 5 recent messages (T108).

    Reduces token usage by 20-40% while preserving recent context.
    """
    if not memory_context:
        return ""

    # If first item is a summary (has 'summary' field), use it
    compressed = []
    if memory_context and "summary" in memory_context[0]:
        compressed.append(f"📋 {memory_context[0].get('summary', '')}")

    # Add last 5 messages
    recent_messages = memory_context[-5:] if len(memory_context) > 5 else memory_context
    for ctx in recent_messages:
        if "summary" not in ctx:  # Skip if it's the summary we already added
            text = ctx.get("text", ctx.get("summary", ""))
            if text:
                compressed.append(f"- {text}")

    return "\n".join(compressed)
