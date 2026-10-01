"""support_answer_node — final reply for the Spacely support graph.

Universal trace point of the support graph (every path writes model_traces).
Shares the bookkeeping helpers of the shop answer node (trace, cache write,
episodic memory, memory-context compression) but none of its sales paths:
no catalog fallback, no follow-up order status, no tool loop, no premium
cascade, no CTA.

Paths:
  0. response already set (support_clarify_node)  → trace only
  1. cached_answer                                → return cache, no LLM
  2. smalltalk_fastpath                           → template, no LLM
  3. declined (confidence L1/L2)                  → persona.decline_message
  4. SMALLTALK                                    → economy-chat, no context
  5. INFO_QUERY / PRICING / COMPLAINT             → economy-chat on
     "Tài liệu Spacely" context (+ memory, + complaint note), groundedness
     self-check (skipped for COMPLAINT: procedural, not factual) → cache
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, cast

from langchain_core.messages import AIMessage

from core.agent.nodes.answer import (
    _compress_context,
    _write_cache,
    _write_episodic_event,
    _write_model_trace,
)
from core.config import settings
from core.support.persona import SPACELY_SUPPORT
from services.ai import AIGateway, extract_llm_metrics

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.agent.state import AgentState
    from services.ai import LLMUsageMetrics

logger = logging.getLogger(__name__)

_ANSWER_MODEL = "economy-chat"
_FACTUAL_INTENTS = frozenset({"INFO_QUERY", "PRICING"})


def _reply(text: str, model_used: str | None, **extra) -> dict:
    return {
        "messages": [AIMessage(content=text)],
        "response": text,
        "model_used": model_used,
        **extra,
    }


def _build_messages(state: AgentState) -> tuple[list[dict[str, str]], str]:
    """System + user messages for path 4/5; returns (messages, context_text)."""
    persona = SPACELY_SUPPORT
    intent = state.get("intent")
    user_q = state["user_message"]

    if intent == "SMALLTALK":
        return (
            [
                {"role": "system", "content": persona.smalltalk_system_prompt},
                {"role": "user", "content": user_q},
            ],
            "",
        )

    chunks = state.get("retrieved_chunks") or []
    chunk_text = "\n\n".join(c.get("text", "") for c in chunks if c.get("text"))

    memory_note = ""
    if state.get("memory_context"):
        memory_text = (
            _compress_context(state["memory_context"])
            if state.get("thread_summary_exists")
            else "\n".join(
                f"- {m.get('summary_text') or m.get('summary') or m.get('text', '')}"
                for m in state["memory_context"]
            )
        )
        memory_note = f"\n[Ngữ cảnh từ các cuộc hội thoại trước]:\n{memory_text}"

    complaint_note = persona.complaint_note if intent == "COMPLAINT" else ""
    system_prompt = f"{persona.answer_system_prompt}{complaint_note}{memory_note}"
    prompt = f"{persona.context_label}:\n{chunk_text}\n\nCâu hỏi: {user_q}"
    return (
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        chunk_text,
    )


async def _generate(messages: list[dict[str, str]]) -> tuple[str, LLMUsageMetrics]:
    start = time.perf_counter()
    result = await AIGateway.complete(model=_ANSWER_MODEL, messages=messages)
    metrics = extract_llm_metrics(result, latency_ms=(time.perf_counter() - start) * 1000)
    return (result.choices[0].message.content or "").strip(), metrics


async def _ground(
    state: AgentState, messages: list[dict[str, str]], response: str, context: str
) -> tuple[str, bool, dict]:
    """WP-V2-1 self-check, support flavour: verify → one strict regen → decline.

    Returns (response, declined, meta). Any judge error passes the answer
    through (same fail-open as the shop node).
    """
    from services.rag.groundedness import STRICT_GROUNDING_SUFFIX, check_groundedness

    query = state["user_message"]
    try:
        verdict = await check_groundedness(query, response, context)
    except Exception as exc:
        logger.warning("support groundedness judge failed, passing answer: %s", exc)
        return response, False, {"groundedness": "judge_error"}

    meta = {"answerable": verdict.answerable, "supported": verdict.supported, "regen_count": 0}
    if not verdict.answerable:
        return SPACELY_SUPPORT.decline_message, True, meta
    if verdict.supported:
        return response, False, meta

    for attempt in range(1, settings.GROUNDEDNESS_MAX_REGEN + 1):
        strict = [
            {"role": "system", "content": messages[0]["content"] + STRICT_GROUNDING_SUFFIX},
            messages[1],
        ]
        try:
            response, _ = await _generate(strict)
            verdict = await check_groundedness(query, response, context)
        except Exception as exc:
            logger.warning("support groundedness regen %d failed: %s", attempt, exc)
            break
        meta.update(regen_count=attempt, supported=verdict.supported)
        if verdict.supported:
            return response, False, meta
    return (
        SPACELY_SUPPORT.decline_message,
        True,
        {**meta, "unsupported_claims": verdict.unsupported_claims[:5]},
    )


async def support_answer_node(state: AgentState, config: RunnableConfig) -> dict:
    db = cast("AsyncSession | None", config.get("configurable", {}).get("db"))
    persona = SPACELY_SUPPORT
    intent = state.get("intent") or "INFO_QUERY"

    # Path 0 — clarify already produced this turn's response.
    if state.get("response") and state.get("model_used") == "clarify":
        await _write_model_trace(
            state,
            db=db,
            metadata_={
                "guard_decision": "CLARIFY",
                "declined": False,
                "intended_model": "clarify",
            },
        )
        return {"messages": [AIMessage(content=state["response"])]}

    # Path 1 — semantic cache hit (retrieval_node).
    if state.get("cached_answer"):
        await _write_model_trace(
            state,
            db=db,
            metadata_={
                "guard_decision": "CACHE_HIT",
                "declined": False,
                "intended_model": "cache",
            },
        )
        return _reply(state["cached_answer"], "cache")

    # Path 2 — zero-LLM greeting template (support_router_node fast path).
    if state.get("smalltalk_fastpath"):
        await _write_model_trace(
            state,
            db=db,
            metadata_={
                "guard_decision": "SMALLTALK_FASTPATH",
                "declined": False,
                "intended_model": "template",
            },
        )
        return _reply(persona.smalltalk_fastpath_reply, "template")

    # Path 3 — confidence guards said no. Never for COMPLAINT: a complaint with
    # weak FAQ overlap still deserves the apology + human handoff (path 5).
    if state.get("declined") and intent != "COMPLAINT":
        await _write_model_trace(
            state,
            db=db,
            metadata_={"guard_decision": "REJECTED", "declined": True, "intended_model": None},
        )
        return _reply(persona.decline_message, None, declined=True)

    # Paths 4/5 spend tokens → per-customer daily cap (no-op at default config).
    from services.costs import check_budget

    budget = await check_budget(state.get("customer_id"), db)
    if budget.over_customer_cap:
        await _write_model_trace(
            state,
            db=db,
            metadata_={
                "guard_decision": "CUSTOMER_CAP",
                "declined": False,
                "intended_model": "customer_cap",
            },
        )
        return _reply(persona.customer_cap_message, None)

    messages, context = _build_messages(state)
    try:
        response, metrics = await _generate(messages)
    except Exception as exc:
        logger.error("support_answer generation failed: %s", exc)
        await _write_model_trace(
            state,
            db=db,
            metadata_={
                "guard_decision": "LLM_FAILED",
                "declined": False,
                "intended_model": _ANSWER_MODEL,
            },
        )
        return _reply(
            persona.holding_message,
            None,
            risk_signals=[*(state.get("risk_signals") or []), "degraded"],
        )

    if not response:
        response = persona.decline_message

    declined = False
    ground_meta: dict = {}
    if settings.GROUNDEDNESS_CHECK_ENABLED and intent in _FACTUAL_INTENTS and context:
        response, declined, ground_meta = await _ground(state, messages, response, context)

    if (
        not declined
        and intent in _FACTUAL_INTENTS
        and db is not None
        and state.get("canonical_query")
        and state.get("query_vector")
    ):
        await _write_cache(state, response, db)

    await _write_model_trace(
        state,
        db=db,
        metadata_={
            "guard_decision": "GROUNDEDNESS_REJECTED" if declined else "ACCEPTED",
            "declined": declined,
            "intended_model": _ANSWER_MODEL,
            **ground_meta,
        },
        metrics=metrics,
    )
    if not declined:
        await _write_episodic_event(state, response, db)

    return _reply(response, _ANSWER_MODEL, declined=declined)
