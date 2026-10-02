"""support_answer_node + support_clarify_node — every path, LLM mocked."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.agent.state import make_initial_state
from core.support.nodes.answer import support_answer_node
from core.support.nodes.clarify import FALLBACK_CLARIFY_QUESTION, support_clarify_node
from core.support.persona import SPACELY_SUPPORT as P
from services.rag.groundedness import GroundednessVerdict

_NS = "core.support.nodes.answer"
_CFG = {"configurable": {"db": None}}


def _llm(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


def _budget_ok():
    return SimpleNamespace(
        over_customer_cap=False, over_daily_budget=False, customer_calls_today=0
    )


def _state(msg: str, intent: str, **kw):
    s = make_initial_state(msg, "s1", "c1")
    s["intent"] = intent
    s.update(kw)
    return s


@pytest.fixture(autouse=True)
def _no_side_effects():
    with (
        patch(f"{_NS}._write_model_trace", new=AsyncMock()),
        patch(f"{_NS}._write_cache", new=AsyncMock()),
        patch(f"{_NS}._write_episodic_event", new=AsyncMock()),
        patch("services.costs.check_budget", new=AsyncMock(return_value=_budget_ok())),
    ):
        yield


@pytest.mark.asyncio
async def test_path0_clarify_response_passes_through():
    s = _state("x", "INFO_QUERY", response="Bạn hỏi về A hay B?", model_used="clarify")
    with patch("services.ai.AIGateway.complete", new=AsyncMock()) as llm:
        out = await support_answer_node(s, _CFG)
    llm.assert_not_awaited()
    assert out["messages"][0].content == "Bạn hỏi về A hay B?"
    assert "response" not in out  # không ghi đè response của clarify


@pytest.mark.asyncio
async def test_path1_cache_hit():
    s = _state("credit là gì", "PRICING", cached_answer="Credit là…")
    with patch("services.ai.AIGateway.complete", new=AsyncMock()) as llm:
        out = await support_answer_node(s, _CFG)
    llm.assert_not_awaited()
    assert out["response"] == "Credit là…" and out["model_used"] == "cache"


@pytest.mark.asyncio
async def test_path2_smalltalk_fastpath_template():
    s = _state("xin chào", "SMALLTALK", smalltalk_fastpath=True)
    with patch("services.ai.AIGateway.complete", new=AsyncMock()) as llm:
        out = await support_answer_node(s, _CFG)
    llm.assert_not_awaited()
    assert out["response"] == P.smalltalk_fastpath_reply


@pytest.mark.asyncio
async def test_path3_declined_by_confidence():
    s = _state("thời tiết hôm nay", "INFO_QUERY", declined=True)
    with patch("services.ai.AIGateway.complete", new=AsyncMock()) as llm:
        out = await support_answer_node(s, _CFG)
    llm.assert_not_awaited()
    assert out["response"] == P.decline_message and out["declined"] is True


@pytest.mark.asyncio
async def test_path4_smalltalk_llm_no_context():
    s = _state("bạn khoẻ không", "SMALLTALK")
    with patch(
        "services.ai.AIGateway.complete", new=AsyncMock(return_value=_llm("Mình khoẻ!"))
    ) as llm:
        out = await support_answer_node(s, _CFG)
    sent = llm.await_args.kwargs["messages"]
    assert sent[0]["content"] == P.smalltalk_system_prompt
    assert sent[1]["content"] == "bạn khoẻ không"
    assert out["response"] == "Mình khoẻ!" and out["declined"] is False


@pytest.mark.asyncio
async def test_path5_info_query_grounded_accepted():
    s = _state(
        "clone space được không?",
        "INFO_QUERY",
        retrieved_chunks=[{"text": "Được — bấm Sao chép về học."}],
        canonical_query="clone space",
        query_vector=[0.1],
    )
    ok = GroundednessVerdict(answerable=True, supported=True)
    with (
        patch(
            "services.ai.AIGateway.complete",
            new=AsyncMock(return_value=_llm("Được, bạn bấm Sao chép về học.")),
        ) as llm,
        patch("services.rag.groundedness.check_groundedness", new=AsyncMock(return_value=ok)),
        patch(f"{_NS}._write_cache", new=AsyncMock()) as cache,
    ):
        out = await support_answer_node(s, {"configurable": {"db": object()}})
    sent = llm.await_args.kwargs["messages"]
    assert sent[0]["content"].startswith(P.answer_system_prompt)
    assert P.complaint_note not in sent[0]["content"]
    assert sent[1]["content"].startswith("Tài liệu Spacely:\nĐược — bấm Sao chép về học.")
    assert out["declined"] is False and out["model_used"] == "economy-chat"
    cache.assert_awaited_once()


@pytest.mark.asyncio
async def test_path5_not_answerable_declines_without_regen():
    s = _state("spacely có bán laptop không", "INFO_QUERY", retrieved_chunks=[{"text": "FAQ…"}])
    bad = GroundednessVerdict(answerable=False, supported=True)
    with (
        patch(
            "services.ai.AIGateway.complete",
            new=AsyncMock(return_value=_llm("Có bán laptop giá 20 triệu.")),
        ) as llm,
        patch("services.rag.groundedness.check_groundedness", new=AsyncMock(return_value=bad)),
    ):
        out = await support_answer_node(s, _CFG)
    assert llm.await_count == 1
    assert out["response"] == P.decline_message and out["declined"] is True


@pytest.mark.asyncio
async def test_path5_unsupported_regenerates_once_then_declines():
    s = _state("mỗi lượt mấy credit", "PRICING", retrieved_chunks=[{"text": "Có lượt miễn phí."}])
    unsupported = GroundednessVerdict(
        answerable=True, supported=False, unsupported_claims=["10 credit"]
    )
    with (
        patch(
            "services.ai.AIGateway.complete",
            new=AsyncMock(return_value=_llm("Mỗi lượt 10 credit.")),
        ) as llm,
        patch(
            "services.rag.groundedness.check_groundedness", new=AsyncMock(return_value=unsupported)
        ) as judge,
        patch("core.support.nodes.answer.settings.GROUNDEDNESS_MAX_REGEN", 1),
    ):
        out = await support_answer_node(s, _CFG)
    assert llm.await_count == 2  # first answer + one strict regen
    assert "QUAN TRỌNG" in llm.await_args.kwargs["messages"][0]["content"]
    assert judge.await_count == 2
    assert out["declined"] is True and out["response"] == P.decline_message


@pytest.mark.asyncio
async def test_path5_complaint_adds_note_and_skips_groundedness():
    s = _state(
        "tạo quiz xong mất credit mà không ra gì",
        "COMPLAINT",
        retrieved_chunks=[{"text": "Hoàn tiền: …"}],
    )
    with (
        patch(
            "services.ai.AIGateway.complete",
            new=AsyncMock(return_value=_llm("Mình xin lỗi… bấm Liên hệ hỗ trợ nhé.")),
        ) as llm,
        patch("services.rag.groundedness.check_groundedness", new=AsyncMock()) as judge,
        patch(f"{_NS}._write_cache", new=AsyncMock()) as cache,
    ):
        out = await support_answer_node(s, {"configurable": {"db": object()}})
    assert P.complaint_note in llm.await_args.kwargs["messages"][0]["content"]
    judge.assert_not_awaited()
    cache.assert_not_awaited()  # không cache câu khiếu nại
    assert out["declined"] is False


@pytest.mark.asyncio
async def test_llm_failure_returns_holding_message():
    s = _state("credit là gì", "PRICING", retrieved_chunks=[{"text": "…"}])
    with patch("services.ai.AIGateway.complete", new=AsyncMock(side_effect=RuntimeError("down"))):
        out = await support_answer_node(s, _CFG)
    assert out["response"] == P.holding_message and out["model_used"] is None
    assert "degraded" in out["risk_signals"]


@pytest.mark.asyncio
async def test_customer_cap_message():
    s = _state("credit là gì", "PRICING")
    capped = SimpleNamespace(
        over_customer_cap=True, over_daily_budget=False, customer_calls_today=99
    )
    with (
        patch("services.costs.check_budget", new=AsyncMock(return_value=capped)),
        patch("services.ai.AIGateway.complete", new=AsyncMock()) as llm,
    ):
        out = await support_answer_node(s, _CFG)
    llm.assert_not_awaited()
    assert out["response"] == P.customer_cap_message


@pytest.mark.asyncio
async def test_clarify_node_uses_support_prompt_and_candidates():
    s = _state("cái đó làm sao", "INFO_QUERY", clarify_count=0)
    s["citations"] = [{"name": "Credit là gì, mua sao?"}, {"name": "Quên mật khẩu?"}]
    result = SimpleNamespace(question="Bạn đang hỏi về credit hay mật khẩu?")
    with patch(
        "services.ai.AIGateway.complete_structured", new=AsyncMock(return_value=result)
    ) as llm:
        out = await support_clarify_node(s, _CFG)
    system = llm.await_args.kwargs["messages"][0]["content"]
    assert "Các chủ đề gần đúng nhất: Credit là gì, mua sao?, Quên mật khẩu?" in system
    assert "anh/chị" not in system.lower() and "sản phẩm" not in system.lower()
    assert out["response"] == result.question and out["awaiting_clarification"] is True
    assert out["clarify_original_query"] == "cái đó làm sao" and out["clarify_count"] == 1


@pytest.mark.asyncio
async def test_clarify_node_fallback_on_llm_error():
    s = _state("cái đó làm sao", "INFO_QUERY")
    with patch(
        "services.ai.AIGateway.complete_structured", new=AsyncMock(side_effect=RuntimeError("x"))
    ):
        out = await support_clarify_node(s, _CFG)
    assert out["response"] == FALLBACK_CLARIFY_QUESTION and out["model_used"] == "clarify"


@pytest.mark.asyncio
async def test_complaint_ignores_confidence_decline():
    s = _state("app lỗi mất credit", "COMPLAINT", declined=True, retrieved_chunks=[])
    with patch(
        "services.ai.AIGateway.complete", new=AsyncMock(return_value=_llm("Mình xin lỗi…"))
    ) as llm:
        out = await support_answer_node(s, _CFG)
    llm.assert_awaited_once()
    assert out["response"] == "Mình xin lỗi…" and out["declined"] is False


@pytest.mark.asyncio
async def test_rate_limit_is_retried_then_succeeds():
    from litellm.exceptions import RateLimitError

    from core.support.nodes import answer as mod

    s = _state("credit là gì", "PRICING", retrieved_chunks=[{"text": "…"}])
    err = RateLimitError(
        "Rate limit… Please try again in 832.5ms.", llm_provider="groq", model="x"
    )
    calls = AsyncMock(side_effect=[err, _llm("Credit là…")])
    with (
        patch("services.ai.AIGateway.complete", new=calls),
        patch.object(mod.asyncio, "sleep", new=AsyncMock()) as slept,
        patch(
            "services.rag.groundedness.check_groundedness",
            new=AsyncMock(return_value=GroundednessVerdict(answerable=True, supported=True)),
        ),
    ):
        out = await support_answer_node(s, _CFG)
    assert calls.await_count == 2
    assert slept.await_args.args[0] == pytest.approx(1.3325)  # 0.8325s + 0.5 headroom
    assert out["response"] == "Credit là…" and out["model_used"] == "economy-chat"


@pytest.mark.asyncio
async def test_rate_limit_exhausted_returns_holding_message():
    from litellm.exceptions import RateLimitError

    from core.support.nodes import answer as mod

    s = _state("credit là gì", "PRICING", retrieved_chunks=[{"text": "…"}])
    err = RateLimitError("Rate limit… try again in 2s", llm_provider="groq", model="x")
    with (
        patch("services.ai.AIGateway.complete", new=AsyncMock(side_effect=err)) as calls,
        patch.object(mod.asyncio, "sleep", new=AsyncMock()),
    ):
        out = await support_answer_node(s, _CFG)
    assert calls.await_count == 3
    assert out["response"] == P.holding_message
