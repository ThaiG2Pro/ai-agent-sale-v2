"""Answer node for LangGraph sales agent (T048-T049).

Why: Universal trace point — all graph paths (accepted AND declined) route here
to ensure tracing happens (FR-008).

What: a dispatcher over the answer paths, in priority order —
  0   business node already responded        → pass through
  0.5 business node failed (error, no reply)  → fixed error text, no LLM
  1   cache hit                                → cached answer, no LLM
  1.2 SMALLTALK fast path                      → template, no LLM
  1.5 FOLLOW_UP status                         → order status from DB, no LLM
  2   declined                                 → catalog fallback or DECLINE_MESSAGE
  3   accepted                                 → LLM generation (_generate)
Prompt construction lives in core.agent.answer_prompt; template responses and the
per-turn writes (trace, cache, episodic) in core.agent.answer_support. Trace writes
stay routed through this module's `_write_model_trace` name (tests patch it).

Cache write happens here (not in retrieval_node) because we only write
the final answer after the correct model (economy or premium) has generated it.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import logfire

from core.agent.answer_prompt import (
    build_context,
    build_messages,
    compress_context as _compress_context,  # noqa: F401
    history_messages as _history_messages,  # noqa: F401
)
from core.agent.answer_support import (
    degraded_turn_response as _degraded_turn_response,
    generate_catalog_response as _generate_catalog_response,
    generate_followup_response as _generate_followup_response,
    write_cache as _write_cache,
    write_episodic_event as _write_episodic_event,
    write_model_trace as _write_model_trace,
)
from core.agent.state import EscalationReasonEnum
from core.config import settings
from services.ai import AIGateway, extract_llm_metrics
from services.rag.constants import DECLINE_MESSAGE

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

    from core.agent.state import AgentState
    from services.ai import LLMUsageMetrics

logger = logging.getLogger(__name__)

# v3-0 P4 (4.1): only advisory intents ride the tool loop — order/HITL keep
# the state machine (mirrors core.agent.tool_loop.ADVISORY_INTENTS).
_ADVISORY_TOOL_LOOP_INTENTS = frozenset({"INFO_QUERY", "PRICING", "COMPARISON", "AVAILABILITY"})

# Customer-facing text when generation fails or a business node reports an
# error — never the raw exception (conventions: 500 detail stays generic).
GENERATION_ERROR_MESSAGE = (
    "Dạ, hệ thống của shop đang gặp chút trục trặc nên em chưa trả lời được ngay. "
    "Anh/chị vui lòng thử lại sau ít phút, hoặc để lại lời nhắn — nhân viên shop sẽ "
    "liên hệ hỗ trợ sớm nhất ạ!"
)

# WP-V2-5: returned instead of an LLM answer when CUSTOMER_DAILY_MSG_CAP is hit.
CUSTOMER_CAP_MESSAGE = (
    "Cảm ơn bạn đã quan tâm! Hôm nay shop đã nhận khá nhiều câu hỏi từ bạn nên "
    "trợ lý cần tạm nghỉ. Bạn vui lòng quay lại vào ngày mai, hoặc để lại lời "
    "nhắn — nhân viên của shop sẽ liên hệ hỗ trợ sớm nhất nhé! 🙏"
)

SMALLTALK_TEMPLATE = (
    "Xin chào! Em là trợ lý bán hàng của shop 🤗 Em có thể tư vấn sản "
    "phẩm điện tử, báo giá và hỗ trợ đặt hàng. Anh/chị đang quan tâm "
    "sản phẩm nào ạ?"
)

_UNSET: Any = object()


async def _reply(
    state: AgentState,
    db,
    text: str,
    *,
    trace: dict,
    model_used: Any = _UNSET,
    episodic: bool = False,
) -> dict:
    """Trace + (optional) episodic write + the standard response update."""
    from langchain_core.messages import AIMessage

    await _write_model_trace(state, db=db, metadata_=trace)
    if episodic:
        await _write_episodic_event(state, text, db)
    update: dict = {"messages": [AIMessage(content=text)], "response": text}
    if model_used is not _UNSET:
        update["model_used"] = model_used
    return update


async def answer_node(state: AgentState, config: RunnableConfig) -> dict:
    """Generate final answer or decline message (T048) — see module docstring."""
    db = (config.get("configurable") or {}).get("db")
    escalation_flag = state.get("escalation_flag", False)

    # Path 0: a business node (order_execution, customer_support, ...) responded.
    if state.get("response"):
        return await _reply(
            state,
            db,
            state["response"],
            trace={
                "guard_decision": "BUSINESS_LOGIC",
                "escalation_flag": escalation_flag,
                "declined": False,
                "intended_model": "business_logic",
            },
            episodic=True,
        )

    # Path 0.5: a business node failed with no response. An LLM answer here
    # could claim the order went through. Retrieval errors set declined=True
    # and keep the decline path below.
    if state.get("error") and not state.get("declined", False):
        logger.warning("answer_node: business error surfaced: %s", state.get("error"))
        return await _reply(
            state,
            db,
            GENERATION_ERROR_MESSAGE,
            trace={
                "guard_decision": "BUSINESS_ERROR",
                "escalation_flag": escalation_flag,
                "declined": False,
                "intended_model": "business_error",
            },
            model_used=None,
        )

    # Path 1: cache hit.
    cached_answer = state.get("cached_answer")
    if cached_answer and not state.get("declined", False):
        return await _reply(
            state,
            db,
            cached_answer,
            trace={
                "guard_decision": "CACHE_HIT",
                "escalation_reason": state.get("escalation_reason"),
                "escalation_failure": state.get("escalation_failure", False),
                "escalation_flag": escalation_flag,
                "declined": False,
                "intended_model": "cache",
            },
            model_used="cache",
            episodic=True,
        )

    # Path 1.2 — v3-0 P4 (T11 4.2): SMALLTALK fast-path template, zero LLM calls.
    if settings.SMALLTALK_FASTPATH_ENABLED and state.get("smalltalk_fastpath"):
        return await _reply(
            state,
            db,
            SMALLTALK_TEMPLATE,
            trace={
                "guard_decision": "SMALLTALK_FASTPATH",
                "escalation_flag": False,
                "declined": False,
                "intended_model": "template",
            },
            model_used="template",
        )

    # Path 1.5: FOLLOW_UP status inquiry ("đặt chưa?").
    if state.get("intent") == "FOLLOW_UP":
        followup_resp = await _generate_followup_response(state, db)
        return await _reply(
            state,
            db,
            followup_resp,
            trace={
                "guard_decision": "FOLLOW_UP_STATUS",
                "escalation_flag": False,
                "declined": False,
                "intended_model": "followup_status",
            },
            model_used="followup_status",
        )

    # Path 2: declined (Layer 1 or Layer 2).
    if state.get("declined", False):
        # SC01: vague browse INFO_QUERY → product catalog instead of a decline.
        if state.get("intent") == "INFO_QUERY" and db:
            catalog_response = await _generate_catalog_response(state, db)
            if catalog_response:
                return await _reply(
                    state,
                    db,
                    catalog_response,
                    trace={
                        "guard_decision": "CATALOG_FALLBACK",
                        "escalation_flag": False,
                        "declined": False,
                        "intended_model": "catalog_fallback",
                    },
                    model_used="catalog_fallback",
                )
        return await _reply(
            state,
            db,
            DECLINE_MESSAGE,
            trace={
                "guard_decision": "REJECTED",
                "escalation_reason": state.get("escalation_reason"),
                "escalation_failure": state.get("escalation_failure", False),
                "escalation_flag": escalation_flag,
                "intended_model": state.get("model_used"),
            },
            model_used=None,
        )

    # Path 3: accepted → LLM generation.
    return await _generate(state, db)


async def _generate(state: AgentState, db) -> dict:
    """Path 3: budget guard → model choice → generation → groundedness → writes."""
    model = state.get("model_used") or "economy-chat"

    # WP-V2-5 budget guard: only this path spends LLM tokens. No-ops at
    # default config (limits = 0); fails open on DB error.
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
                "customer_calls_today": budget.customer_calls_today,
            },
        )
        return {"response": CUSTOMER_CAP_MESSAGE, "model_used": None}

    budget_downgrade = False
    if budget.over_daily_budget and model != "light-chat":
        logfire.warn(
            "Daily cost limit reached ({cost} USD) — downgrading to light-chat",
            cost=budget.daily_cost_usd,
        )
        model = "light-chat"
        budget_downgrade = True

    # WP-V2-5 routing tune: SMALLTALK needs no reasoning → light tier.
    if settings.CHEAP_INTENT_LIGHT_ROUTING and state.get("intent") == "SMALLTALK":
        model = "light-chat"

    chunk_text, citations_text = build_context(state)
    messages = build_messages(state, chunk_text, citations_text)

    # WP-V2-1 cascade (research §7): intent escalations (COMPLAINT/NEGOTIATION)
    # answer on economy-chat first; premium only when groundedness fails.
    # LOW_CONFIDENCE escalations keep premium direct.
    cascade_target: str | None = None
    if (
        settings.CASCADE_VERIFY_ENABLED
        and settings.GROUNDEDNESS_CHECK_ENABLED
        and not budget_downgrade
        and model not in ("economy-chat", "light-chat")
        and state.get("escalation_reason") == EscalationReasonEnum.INTENT_ESCALATION
        and state.get("intent") != "SMALLTALK"
    ):
        cascade_target = model
        model = "economy-chat"

    start_time = time.perf_counter()
    gen = await _call_llm(
        state,
        db,
        messages,
        model=model,
        cascade_target=cascade_target,
        budget_downgrade=budget_downgrade,
        chunk_text=chunk_text,
        start_time=start_time,
    )
    if gen.get("degraded_update") is not None:
        return gen["degraded_update"]
    response, model, metrics = gen["response"], gen["model"], gen["metrics"]
    escalation_failure = gen["escalation_failure"]

    # Guard against raw JSON schema leaks (IntentClassification JSON as text).
    if response and ("primary_intent" in response or "sensitive_intent" in response):
        cat_resp = await _generate_catalog_response(state, db) if db else None
        response = cat_resp or DECLINE_MESSAGE

    # WP-V2-1 groundedness self-check. Skipped for SMALLTALK and when
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

    # Cache only real, grounded generations (metrics None = error fallback).
    if (
        response
        and metrics is not None
        and not grounded_declined
        and db
        and state.get("canonical_query")
        and state.get("query_vector")
    ):
        await _write_cache(state, response, db)

    await _write_model_trace(
        state,
        db=db,
        metadata_={
            "guard_decision": "GROUNDEDNESS_REJECTED" if grounded_declined else "ACCEPTED",
            "escalation_reason": state.get("escalation_reason"),
            "escalation_failure": escalation_failure,
            "escalation_flag": state.get("escalation_flag", False),
            "declined": grounded_declined,
            "intended_model": model,
            "budget_downgrade": budget_downgrade,
            **(groundedness_meta or {}),
        },
        metrics=metrics,
    )

    # WP-V2-4: episodic memory for accepted answers only.
    if not grounded_declined:
        await _write_episodic_event(state, response, db)

    from langchain_core.messages import AIMessage

    update = {
        "messages": [AIMessage(content=response)],
        "response": response,
        "model_used": model,
        "escalation_failure": escalation_failure,
        "declined": grounded_declined,
    }
    if gen["degraded"]:
        # Returned in the update — mutating `state` in a node is not persisted.
        update["risk_signals"] = [*(state.get("risk_signals") or []), "degraded"]
        update["degraded"] = True
    return update


async def _call_llm(
    state: AgentState,
    db,
    messages: list[dict],
    *,
    model: str,
    cascade_target: str | None,
    budget_downgrade: bool,
    chunk_text: str,
    start_time: float,
) -> dict:
    """Generation with the tool loop / fallback ladder / single fallback.

    Returns {response, model, metrics, escalation_failure, degraded,
    degraded_update}; degraded_update is a full node update when every ladder
    rung failed (holding message + support queue).
    """
    out: dict = {
        "response": None,
        "model": model,
        "metrics": None,
        "escalation_failure": state.get("escalation_failure", False),
        "degraded": False,
        "degraded_update": None,
    }

    def _latency() -> float:
        return (time.perf_counter() - start_time) * 1000

    # v3-0 P4 (T08 4.1): ambiguous advisory turns get the bounded premium
    # tool loop (G1-G8) instead of one single-shot.
    if (
        settings.TOOL_LOOP_ENABLED
        and db is not None
        and state.get("escalation_flag")
        and not budget_downgrade
        and (state.get("intent") or "") in _ADVISORY_TOOL_LOOP_INTENTS
    ):
        from core.agent.tool_loop import run_tool_loop

        answer, tl_model = await run_tool_loop(
            state["user_message"], db, context_note=chunk_text[:1500]
        )
        if answer:
            out.update(response=answer, model=tl_model)
            return out

    # v3-0 P3 (T09): intent-aware fallback ladder.
    if settings.RESILIENCE_V3_ENABLED:
        from services import resilience

        turn_started = state.get("turn_started_at") or time.monotonic()
        ladder_res = await resilience.complete_with_ladder(
            messages=messages,
            intent=state.get("intent"),
            db=db,
            deadline=turn_started + settings.TURN_BUDGET_S,
            preferred_model=model,
        )
        if ladder_res.response is None:
            out["degraded_update"] = await _degraded_turn_response(state, db)
            return out
        result = ladder_res.response
        out.update(
            response=result.choices[0].message.content,
            metrics=extract_llm_metrics(result, latency_ms=_latency()),
            model=ladder_res.model_used,
            degraded=bool(ladder_res.degraded),
        )
        return out

    try:
        result = await AIGateway.complete(model=model, messages=messages)
        out.update(
            response=result.choices[0].message.content,
            metrics=extract_llm_metrics(result, latency_ms=_latency()),
        )
        return out
    except Exception as e:
        # T064: premium failed → economy-chat (escalation_failure=True).
        # Cascade inverse: economy first pass failed → reserved premium target.
        alt_model = cascade_target if model == "economy-chat" else "economy-chat"
        if not alt_model:
            logger.error("answer generation failed: %s", e, exc_info=True)
            out.update(response=GENERATION_ERROR_MESSAGE, model=None)
            return out
    try:
        out.update(model=alt_model, escalation_failure=alt_model == "economy-chat")
        result = await AIGateway.complete(model=alt_model, messages=messages)
        out.update(
            response=result.choices[0].message.content,
            metrics=extract_llm_metrics(result, latency_ms=_latency()),
        )
    except Exception:
        logger.error("answer generation failed on fallback model", exc_info=True)
        out.update(response=GENERATION_ERROR_MESSAGE, model=None)
    return out


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

    # Grade against the resolved question ("Dell XPS 15 con đó giá sao") — the
    # raw vague message makes every grounded answer look off-topic.
    question = state.get("resolved_query") or state["user_message"]
    verdict = await check_groundedness(question, response, context)
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
                logger.warning("groundedness regeneration failed: %s", exc)
                break
            verdict = await check_groundedness(question, response, context)

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
