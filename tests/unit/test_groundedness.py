"""Why this exists: WP-V2-1 — unit coverage for the groundedness self-check and
cascade verification without any real LLM/DB (bài học "test xanh feature gãy":
mock chỉ ở boundary AIGateway/ai_router, logic thật chạy đủ).
What it does: Tests the verdict model, the fail-open contract of
check_groundedness, the verify → regen → decline loop in both answer paths
(services/rag/pipeline.answer_with_rag and core/agent/nodes/answer.answer_node),
the cascade escalation, and that kill switches restore pre-WP-V2-1 behavior.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agent.nodes.answer import _verify_grounded, answer_node
from services.rag.groundedness import GroundednessVerdict, check_groundedness

# ── Helpers ──────────────────────────────────────────────────────────────────


def _llm_result(text: str) -> MagicMock:
    result = MagicMock()
    result.choices[0].message.content = text
    result.usage.prompt_tokens = 10
    result.usage.completion_tokens = 20
    result.usage.total_tokens = 30
    return result


def _verdict(answerable: bool = True, supported: bool = True, claims: list | None = None):
    return GroundednessVerdict(
        answerable=answerable, supported=supported, unsupported_claims=claims or []
    )


def _accepted_state(**over) -> dict:
    state = {
        "session_id": "s1",
        "user_message": "Giá Samsung S24 Ultra?",
        "intent": "PRICING",
        "declined": False,
        "retrieved_chunks": [{"text": "[PHONE-SM-001] Samsung S24 Ultra — 24990000 VND"}],
        "citations": [],
        "escalation_flag": False,
        "escalation_reason": "none",
        "model_used": "economy-chat",
    }
    state.update(over)
    return state


# ── GroundednessVerdict / check_groundedness ────────────────────────────────


@pytest.mark.asyncio
async def test_check_groundedness_parses_router_verdict():
    payload = _llm_result('{"answerable": true, "supported": false, "unsupported_claims": ["x"]}')
    with patch("services.rag.groundedness.ai_router") as router:
        router.acompletion = AsyncMock(return_value=payload)
        verdict = await check_groundedness("q", "a", "ctx")
    assert verdict.answerable is True
    assert verdict.supported is False
    assert verdict.unsupported_claims == ["x"]
    # economy tier, structured output, deterministic
    kwargs = router.acompletion.call_args.kwargs
    assert kwargs["model"] == "economy-chat"
    assert kwargs["response_format"] is GroundednessVerdict
    assert kwargs["temperature"] == 0


@pytest.mark.asyncio
async def test_check_groundedness_fails_open_on_llm_error():
    with patch("services.rag.groundedness.ai_router") as router:
        router.acompletion = AsyncMock(side_effect=RuntimeError("boom"))
        verdict = await check_groundedness("q", "a", "ctx")
    assert verdict.answerable is True and verdict.supported is True


@pytest.mark.asyncio
async def test_check_groundedness_empty_answer_short_circuits():
    with patch("services.rag.groundedness.ai_router") as router:
        router.acompletion = AsyncMock()
        verdict = await check_groundedness("q", "   ", "ctx")
    router.acompletion.assert_not_called()
    assert verdict.supported is True


# ── answer_node: groundedness loop ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_answer_node_supported_answer_passes_through():
    state = _accepted_state()
    with (
        patch(
            "core.agent.nodes.answer.AIGateway.complete",
            new=AsyncMock(return_value=_llm_result("Giá 24.990.000 VND")),
        ) as complete,
        patch(
            "core.agent.nodes.answer._verify_grounded",
            new=AsyncMock(return_value=("Giá 24.990.000 VND", "economy-chat", None, False, {})),
        ) as verify,
    ):
        result = await answer_node(state, {"configurable": {}})
    assert result["response"] == "Giá 24.990.000 VND"
    assert result["declined"] is False
    assert complete.await_count == 1
    verify.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_grounded_unsupported_regenerates_then_passes():
    """Verdict fail → 1 strict regen → pass. Second answer is returned."""
    verdicts = [_verdict(supported=False, claims=["giá 99tr"]), _verdict()]
    with (
        patch(
            "services.rag.groundedness.check_groundedness",
            new=AsyncMock(side_effect=verdicts),
        ),
        patch(
            "core.agent.nodes.answer.AIGateway.complete",
            new=AsyncMock(return_value=_llm_result("câu trả lời đã siết")),
        ) as complete,
    ):
        response, _model, _metrics, declined, meta = await _verify_grounded(
            state=_accepted_state(),
            messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}],
            model="economy-chat",
            cascade_target=None,
            response="câu trả lời bịa",
            metrics=None,
            context="ctx",
            start_time=0.0,
        )
    assert declined is False
    assert response == "câu trả lời đã siết"
    assert meta["groundedness"]["regen_count"] == 1
    # strict regen must tighten the system prompt
    strict_sys = complete.call_args.kwargs["messages"][0]["content"]
    assert "NGUYÊN VĂN" in strict_sys


@pytest.mark.asyncio
async def test_verify_grounded_regen_still_unsupported_declines():
    verdicts = [_verdict(supported=False), _verdict(supported=False)]
    with (
        patch(
            "services.rag.groundedness.check_groundedness",
            new=AsyncMock(side_effect=verdicts),
        ),
        patch(
            "core.agent.nodes.answer.AIGateway.complete",
            new=AsyncMock(return_value=_llm_result("vẫn bịa")),
        ),
    ):
        _r, _m, _me, declined, meta = await _verify_grounded(
            state=_accepted_state(),
            messages=[{"role": "system", "content": "sys"}],
            model="economy-chat",
            cascade_target=None,
            response="bịa",
            metrics=None,
            context="ctx",
            start_time=0.0,
        )
    assert declined is True
    assert meta["groundedness"]["regen_count"] == 1  # budget default 1


@pytest.mark.asyncio
async def test_verify_grounded_unanswerable_declines_without_regen():
    """Out-of-catalog subject → decline immediately, no regen LLM call."""
    with (
        patch(
            "services.rag.groundedness.check_groundedness",
            new=AsyncMock(return_value=_verdict(answerable=False)),
        ),
        patch("core.agent.nodes.answer.AIGateway.complete", new=AsyncMock()) as complete,
    ):
        _r, _m, _me, declined, meta = await _verify_grounded(
            state=_accepted_state(user_message="Shop có tủ lạnh không?"),
            messages=[{"role": "system", "content": "sys"}],
            model="economy-chat",
            cascade_target=None,
            response="Chúng tôi không có tủ lạnh...",
            metrics=None,
            context="ctx",
            start_time=0.0,
        )
    assert declined is True
    assert meta["groundedness"]["regen_count"] == 0
    complete.assert_not_called()


@pytest.mark.asyncio
async def test_answer_node_groundedness_decline_returns_decline_message():
    from services.rag.constants import DECLINE_MESSAGE

    state = _accepted_state()
    with (
        patch(
            "core.agent.nodes.answer.AIGateway.complete",
            new=AsyncMock(return_value=_llm_result("bịa giá")),
        ),
        patch(
            "core.agent.nodes.answer._verify_grounded",
            new=AsyncMock(return_value=("bịa giá", "economy-chat", None, True, {})),
        ),
    ):
        result = await answer_node(state, {"configurable": {}})
    assert result["response"] == DECLINE_MESSAGE
    assert result["declined"] is True


@pytest.mark.asyncio
async def test_answer_node_kill_switch_skips_check_entirely():
    state = _accepted_state()
    with (
        patch("core.agent.nodes.answer.settings.GROUNDEDNESS_CHECK_ENABLED", False),
        patch(
            "core.agent.nodes.answer.AIGateway.complete",
            new=AsyncMock(return_value=_llm_result("trả lời")),
        ) as complete,
        patch("core.agent.nodes.answer._verify_grounded", new=AsyncMock()) as verify,
    ):
        result = await answer_node(state, {"configurable": {}})
    assert result["response"] == "trả lời"
    assert result["declined"] is False
    assert complete.await_count == 1  # exactly the one generation call — old behavior
    verify.assert_not_called()


@pytest.mark.asyncio
async def test_answer_node_smalltalk_skips_check():
    state = _accepted_state(intent="SMALLTALK", retrieved_chunks=[])
    with (
        patch(
            "core.agent.nodes.answer.AIGateway.complete",
            new=AsyncMock(return_value=_llm_result("chào bạn")),
        ),
        patch("core.agent.nodes.answer._verify_grounded", new=AsyncMock()) as verify,
    ):
        result = await answer_node(state, {"configurable": {}})
    assert result["response"] == "chào bạn"
    verify.assert_not_called()


# ── answer_node: cascade verification ────────────────────────────────────────


def _escalated_state() -> dict:
    return _accepted_state(
        intent="COMPLAINT",
        escalation_flag=True,
        escalation_reason="intent_escalation",
        model_used="premium-chat",
    )


@pytest.mark.asyncio
async def test_cascade_intent_escalation_starts_on_economy():
    """COMPLAINT with cascade on: first generation call uses economy-chat, and a
    grounded answer never touches premium."""
    state = _escalated_state()
    with (
        patch(
            "core.agent.nodes.answer.AIGateway.complete",
            new=AsyncMock(return_value=_llm_result("xin lỗi quý khách")),
        ) as complete,
        patch(
            "services.rag.groundedness.check_groundedness",
            new=AsyncMock(return_value=_verdict()),
        ),
    ):
        result = await answer_node(state, {"configurable": {}})
    assert complete.await_count == 1
    assert complete.call_args.kwargs["model"] == "economy-chat"
    assert result["model_used"] == "economy-chat"
    assert result["declined"] is False


@pytest.mark.asyncio
async def test_cascade_groundedness_fail_retries_on_premium():
    verdicts = [_verdict(supported=False), _verdict()]
    with (
        patch(
            "services.rag.groundedness.check_groundedness",
            new=AsyncMock(side_effect=verdicts),
        ),
        patch(
            "core.agent.nodes.answer.AIGateway.complete",
            new=AsyncMock(return_value=_llm_result("premium trả lời")),
        ) as complete,
    ):
        response, model, _me, declined, meta = await _verify_grounded(
            state=_escalated_state(),
            messages=[{"role": "system", "content": "sys"}],
            model="economy-chat",
            cascade_target="premium-chat",
            response="economy trả lời",
            metrics=None,
            context="ctx",
            start_time=0.0,
        )
    assert declined is False
    assert model == "premium-chat"
    assert response == "premium trả lời"
    assert meta["groundedness"]["cascade_escalated"] is True
    assert complete.call_args.kwargs["model"] == "premium-chat"


@pytest.mark.asyncio
async def test_cascade_retry_budgeted_even_with_regen_zero():
    """GROUNDEDNESS_MAX_REGEN=0 must not disable the cascade's premium retry."""
    verdicts = [_verdict(supported=False), _verdict()]
    with (
        patch("core.agent.nodes.answer.settings.GROUNDEDNESS_MAX_REGEN", 0),
        patch(
            "services.rag.groundedness.check_groundedness",
            new=AsyncMock(side_effect=verdicts),
        ),
        patch(
            "core.agent.nodes.answer.AIGateway.complete",
            new=AsyncMock(return_value=_llm_result("premium")),
        ),
    ):
        _r, model, _me, declined, _meta = await _verify_grounded(
            state=_escalated_state(),
            messages=[{"role": "system", "content": "sys"}],
            model="economy-chat",
            cascade_target="premium-chat",
            response="economy",
            metrics=None,
            context="ctx",
            start_time=0.0,
        )
    assert declined is False
    assert model == "premium-chat"


@pytest.mark.asyncio
async def test_cascade_kill_switch_premium_goes_direct():
    state = _escalated_state()
    with (
        patch("core.agent.nodes.answer.settings.CASCADE_VERIFY_ENABLED", False),
        patch(
            "core.agent.nodes.answer.AIGateway.complete",
            new=AsyncMock(return_value=_llm_result("premium trả lời")),
        ) as complete,
        patch(
            "core.agent.nodes.answer._verify_grounded",
            new=AsyncMock(return_value=("premium trả lời", "premium-chat", None, False, {})),
        ),
    ):
        result = await answer_node(state, {"configurable": {}})
    assert complete.call_args.kwargs["model"] == "premium-chat"
    assert result["model_used"] == "premium-chat"


@pytest.mark.asyncio
async def test_cascade_not_applied_to_low_confidence_escalation():
    state = _escalated_state()
    state["escalation_reason"] = "low_confidence"
    with (
        patch(
            "core.agent.nodes.answer.AIGateway.complete",
            new=AsyncMock(return_value=_llm_result("premium trả lời")),
        ) as complete,
        patch(
            "core.agent.nodes.answer._verify_grounded",
            new=AsyncMock(return_value=("premium trả lời", "premium-chat", None, False, {})),
        ),
    ):
        await answer_node(state, {"configurable": {}})
    assert complete.call_args.kwargs["model"] == "premium-chat"


@pytest.mark.asyncio
async def test_cascade_economy_failure_falls_forward_to_premium():
    """Cascade first pass (economy) blows up → reserved premium target is used,
    escalation_failure stays False (this is an upgrade, not a degradation)."""
    state = _escalated_state()
    calls = {"n": 0}

    async def flaky_complete(model, messages):
        calls["n"] += 1
        if model == "economy-chat":
            raise RuntimeError("economy down")
        return _llm_result("premium cứu")

    with (
        patch(
            "core.agent.nodes.answer.AIGateway.complete",
            new=AsyncMock(side_effect=flaky_complete),
        ),
        patch(
            "services.rag.groundedness.check_groundedness",
            new=AsyncMock(return_value=_verdict()),
        ),
    ):
        result = await answer_node(state, {"configurable": {}})
    assert result["response"] == "premium cứu"
    assert result["model_used"] == "premium-chat"
    assert result["escalation_failure"] is False


# ── answer_with_rag: pipeline integration (mocked boundaries) ────────────────


def _retrieval_result(**over):
    from services.rag.pipeline import RetrievalResult

    base = dict(
        cached_answer=None,
        cached_citations=[],
        declined=False,
        citations=[
            {
                "product_id": "p1",
                "chunk_id": "c1",
                "sku": "PHONE-SM-001",
                "name": "Samsung S24 Ultra",
                "source_text": "[PHONE-SM-001] Samsung S24 Ultra\nGiá: 24,990,000 VND\nFlagship",
            }
        ],
        chunks=[{"text": "chunk"}],
        best_similarity=0.9,
        similarity_gap=0.2,
        canonical_query="giá samsung s24 ultra",
        query_vector=[0.1] * 4,
        query_category="SIMPLE",
        top_k_used=10,
    )
    base.update(over)
    return RetrievalResult(**base)


@pytest.mark.asyncio
async def test_answer_with_rag_groundedness_decline_flips_declined_true():
    """Out-of-catalog slips past similarity guard → verdict answerable=False →
    RAGResult.declined=True with DECLINE_MESSAGE (đây là fix cho out_of_catalog 0/4)."""
    import services.rag.pipeline as pipeline_mod
    from services.rag.constants import DECLINE_MESSAGE

    db = AsyncMock()
    with (
        patch.object(
            pipeline_mod, "retrieve_with_retry", new=AsyncMock(return_value=_retrieval_result())
        ),
        patch.object(
            pipeline_mod.AIGateway,
            "complete",
            new=AsyncMock(return_value=_llm_result("Dạ shop không có tủ lạnh...")),
        ),
        patch(
            "services.rag.groundedness.check_groundedness",
            new=AsyncMock(return_value=_verdict(answerable=False)),
        ),
        patch.object(pipeline_mod, "set_cache", new=AsyncMock()) as cache_write,
        patch.object(pipeline_mod, "_write_model_trace", new=AsyncMock()) as trace,
    ):
        rag = await pipeline_mod.answer_with_rag(db, "Shop có tủ lạnh không?")
    assert rag.declined is True
    assert rag.answer == DECLINE_MESSAGE
    assert rag.citations == []
    cache_write.assert_not_called()  # unverified answers are never cached
    assert trace.call_args.kwargs["guard_decision"] == "GROUNDEDNESS_REJECTED"


@pytest.mark.asyncio
async def test_answer_with_rag_supported_answer_cached_and_accepted():
    import services.rag.pipeline as pipeline_mod

    db = AsyncMock()
    with (
        patch.object(
            pipeline_mod, "retrieve_with_retry", new=AsyncMock(return_value=_retrieval_result())
        ),
        patch.object(
            pipeline_mod.AIGateway,
            "complete",
            new=AsyncMock(return_value=_llm_result("Giá 24.990.000 VND ạ")),
        ),
        patch(
            "services.rag.groundedness.check_groundedness",
            new=AsyncMock(return_value=_verdict()),
        ),
        patch.object(pipeline_mod, "set_cache", new=AsyncMock()) as cache_write,
        patch.object(pipeline_mod, "_write_model_trace", new=AsyncMock()) as trace,
    ):
        rag = await pipeline_mod.answer_with_rag(db, "Giá Samsung S24 Ultra?")
    assert rag.declined is False
    assert rag.answer == "Giá 24.990.000 VND ạ"
    cache_write.assert_called_once()
    assert trace.call_args.kwargs["guard_decision"] == "ACCEPTED"
    assert trace.call_args.kwargs["extra_metadata"]["groundedness"]["supported"] is True


@pytest.mark.asyncio
async def test_answer_with_rag_kill_switch_no_check_call():
    import services.rag.pipeline as pipeline_mod

    db = AsyncMock()
    with (
        patch("core.config.settings.GROUNDEDNESS_CHECK_ENABLED", False),
        patch.object(
            pipeline_mod, "retrieve_with_retry", new=AsyncMock(return_value=_retrieval_result())
        ),
        patch.object(
            pipeline_mod.AIGateway,
            "complete",
            new=AsyncMock(return_value=_llm_result("trả lời")),
        ) as complete,
        patch("services.rag.groundedness.check_groundedness", new=AsyncMock()) as check,
        patch.object(pipeline_mod, "set_cache", new=AsyncMock()),
        patch.object(pipeline_mod, "_write_model_trace", new=AsyncMock()) as trace,
    ):
        rag = await pipeline_mod.answer_with_rag(db, "Giá Samsung S24 Ultra?")
    assert rag.declined is False
    check.assert_not_called()
    assert complete.await_count == 1
    assert trace.call_args.kwargs["extra_metadata"] is None
