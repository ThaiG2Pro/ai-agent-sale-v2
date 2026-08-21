"""Unit tests for v3-0 P4 (T08/T11): SMALLTALK fast-path, keyword whitelist,
conditional memory skip, and the bounded premium tool loop."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agent.nodes.answer import answer_node
from core.agent.nodes.memory_retrieval import memory_retrieval_node
from core.agent.nodes.router import _smalltalk_fastpath, _whitelist_classify, router_node
from core.agent.state import IntentEnum, make_initial_state
from core.agent.tool_loop import run_tool_loop
from core.config import settings
from services import resilience


@pytest.fixture(autouse=True)
def _reset():
    resilience.reset_for_tests()
    yield
    resilience.reset_for_tests()


def _llm_response(content: str = "ok", tool_calls=None):
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


# ---------- 4.2 SMALLTALK fast-path gate ----------


def test_smalltalk_fastpath_full_match():
    assert _smalltalk_fastpath("xin chào") is True
    assert _smalltalk_fastpath("  Chào Shop ") is True
    assert _smalltalk_fastpath("hello") is True


def test_smalltalk_fastpath_rejects_business_tokens():
    assert _smalltalk_fastpath("chào shop, giá iphone?") is False
    assert _smalltalk_fastpath("mua hàng") is False


def test_smalltalk_fastpath_rejects_long_or_nonmatching():
    assert _smalltalk_fastpath("xin chào shop mình cần tư vấn laptop") is False
    assert _smalltalk_fastpath("laptop nào tốt") is False
    assert _smalltalk_fastpath("") is False


@pytest.mark.asyncio
async def test_router_smalltalk_fastpath_skips_llm(monkeypatch):
    monkeypatch.setattr(settings, "SMALLTALK_FASTPATH_ENABLED", True)
    state = make_initial_state("xin chào", "s-p4-1", "c1")
    with patch(
        "core.agent.nodes.router.AIGateway.complete",
        new=AsyncMock(side_effect=AssertionError("router LLM must not be called")),
    ):
        cmd = await router_node(state)
    assert cmd.goto == "answer_node"
    assert cmd.update["intent"] == IntentEnum.SMALLTALK.value
    assert cmd.update["smalltalk_fastpath"] is True


@pytest.mark.asyncio
async def test_router_smalltalk_fastpath_kill_switch(monkeypatch):
    monkeypatch.setattr(settings, "SMALLTALK_FASTPATH_ENABLED", False)
    monkeypatch.setattr(settings, "PRECLASSIFY_WHITELIST_ENABLED", False)
    state = make_initial_state("xin chào", "s-p4-2", "c1")
    with patch(
        "core.agent.nodes.router.AIGateway.complete",
        new=AsyncMock(
            return_value=_llm_response(
                '{"primary_intent": "SMALLTALK", "secondary_intents": [], '
                '"confidence": 0.95, "reasoning": "greeting"}'
            )
        ),
    ) as comp:
        cmd = await router_node(state)
    comp.assert_awaited()  # kill switch off → LLM path used
    assert cmd.update.get("smalltalk_fastpath") is not True


@pytest.mark.asyncio
async def test_answer_template_branch_zero_llm(monkeypatch):
    monkeypatch.setattr(settings, "SMALLTALK_FASTPATH_ENABLED", True)
    state = make_initial_state("xin chào", "s-p4-3", "c1")
    state["intent"] = "SMALLTALK"
    state["smalltalk_fastpath"] = True
    with patch(
        "core.agent.nodes.answer.AIGateway.complete",
        new=AsyncMock(side_effect=AssertionError("answer LLM must not be called")),
    ):
        result = await answer_node(state, {"configurable": {"db": None}})
    assert result["model_used"] == "template"
    assert "trợ lý" in result["response"]


# ---------- 4.3 keyword whitelist ----------


def test_whitelist_pricing_availability_info():
    assert _whitelist_classify("iphone 15 giá bao nhiêu?") == IntentEnum.PRICING
    assert _whitelist_classify("laptop dell còn hàng không?") == IntentEnum.AVAILABILITY
    assert _whitelist_classify("shop có sản phẩm gì?") == IntentEnum.INFO_QUERY


def test_whitelist_blockers_force_llm_path():
    # order/negotiation/complaint tokens must NEVER take the whitelist shortcut
    assert _whitelist_classify("mua iphone giá bao nhiêu") is None
    assert _whitelist_classify("giảm giá được không, giá bao nhiêu") is None
    assert _whitelist_classify("hàng lỗi, còn hàng không?") is None
    assert _whitelist_classify("tư vấn giúp mình") is None


@pytest.mark.asyncio
async def test_router_whitelist_skips_llm(monkeypatch):
    monkeypatch.setattr(settings, "PRECLASSIFY_WHITELIST_ENABLED", True)
    state = make_initial_state("iphone 15 giá bao nhiêu?", "s-p4-4", "c1")
    with patch(
        "core.agent.nodes.router.AIGateway.complete",
        new=AsyncMock(side_effect=AssertionError("router LLM must not be called")),
    ):
        cmd = await router_node(state)
    assert cmd.goto == "retrieval_node"
    assert cmd.update["intent"] == IntentEnum.PRICING.value


# ---------- 4.4 conditional memory skip ----------


def _mem_config():
    return {"configurable": {"db": AsyncMock()}}


@pytest.mark.asyncio
async def test_memory_skip_on_high_similarity(monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_SKIP_ENABLED", True)
    state = make_initial_state("giá iphone 15", "s-p4-5", "c1")
    state["intent"] = "PRICING"
    state["similarity_score"] = 0.95
    with patch(
        "services.memory.semantic_memory.SemanticMemoryService.retrieve",
        new=AsyncMock(side_effect=AssertionError("retrieve must be skipped")),
    ):
        result = await memory_retrieval_node(state, _mem_config())
    assert result == {"memory_context": [], "memory_retrieval_scores": []}


@pytest.mark.asyncio
async def test_memory_never_skipped_for_time_reference(monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_SKIP_ENABLED", True)
    state = make_initial_state("đơn hôm qua của tôi sao rồi", "s-p4-6", "c1")
    state["intent"] = "INFO_QUERY"
    state["similarity_score"] = 0.99  # would skip without the time-ref guard
    with patch(
        "services.memory.semantic_memory.SemanticMemoryService.retrieve",
        new=AsyncMock(return_value=[]),
    ) as retrieve:
        await memory_retrieval_node(state, _mem_config())
    retrieve.assert_awaited()


@pytest.mark.asyncio
async def test_memory_never_skipped_for_follow_up(monkeypatch):
    monkeypatch.setattr(settings, "MEMORY_SKIP_ENABLED", True)
    state = make_initial_state("đặt chưa?", "s-p4-7", "c1")
    state["intent"] = "FOLLOW_UP"
    state["similarity_score"] = 0.99
    with patch(
        "services.memory.semantic_memory.SemanticMemoryService.retrieve",
        new=AsyncMock(return_value=[]),
    ) as retrieve:
        await memory_retrieval_node(state, _mem_config())
    retrieve.assert_awaited()


# ---------- 4.1 tool loop ----------


@pytest.mark.asyncio
async def test_tool_loop_kill_switch(monkeypatch):
    monkeypatch.setattr(settings, "TOOL_LOOP_ENABLED", False)
    answer, model = await run_tool_loop("laptop nào tốt?", AsyncMock())
    assert (answer, model) == (None, None)


@pytest.mark.asyncio
async def test_tool_loop_direct_answer(monkeypatch):
    monkeypatch.setattr(settings, "TOOL_LOOP_ENABLED", True)
    with (
        patch.object(resilience, "premium_budget_exhausted", new=AsyncMock(return_value=False)),
        patch(
            "services.ai.AIGateway.complete",
            new=AsyncMock(return_value=_llm_response("Dạ em tư vấn ạ")),
        ),
    ):
        answer, model = await run_tool_loop("laptop nào tốt?", AsyncMock())
    assert answer == "Dạ em tư vấn ạ"
    assert model == "premium-tool-loop"


@pytest.mark.asyncio
async def test_tool_loop_executes_read_only_tool_then_answers(monkeypatch):
    monkeypatch.setattr(settings, "TOOL_LOOP_ENABLED", True)
    tc = SimpleNamespace(
        id="tc1",
        function=SimpleNamespace(
            name="search_products", arguments='{"query": "laptop mỏng nhẹ", "top_k": 2}'
        ),
    )
    responses = [
        _llm_response("", tool_calls=[tc]),
        _llm_response("Dạ shop có 2 mẫu phù hợp ạ"),
    ]
    with (
        patch.object(resilience, "premium_budget_exhausted", new=AsyncMock(return_value=False)),
        patch("services.ai.AIGateway.complete", new=AsyncMock(side_effect=responses)),
        patch(
            "services.rag.retrieval.search_products",
            new=AsyncMock(return_value=[{"name": "X13", "sku": "L1", "price": 25_000_000}]),
        ) as search,
    ):
        answer, model = await run_tool_loop("laptop mỏng nhẹ nào tốt?", AsyncMock())
    search.assert_awaited_once()
    assert model == "premium-tool-loop"
    assert "2 mẫu" in answer


@pytest.mark.asyncio
async def test_tool_loop_429_falls_back_to_local_single_shot(monkeypatch):
    """G8/429 guardrail: cloud 429 → qwen3-4b single shot, premium cooled."""
    monkeypatch.setattr(settings, "TOOL_LOOP_ENABLED", True)

    async def side_effect(*args, **kwargs):
        if kwargs["model"] == "premium-chat":
            raise Exception("RateLimitError: 429")
        return _llm_response("trả lời local")

    with (
        patch.object(resilience, "premium_budget_exhausted", new=AsyncMock(return_value=False)),
        patch("services.ai.AIGateway.complete", new=AsyncMock(side_effect=side_effect)),
    ):
        answer, model = await run_tool_loop("laptop nào tốt?", AsyncMock())
    assert answer == "trả lời local"
    assert model == "qwen3-4b"
    assert not resilience._rung_available("premium-chat")


@pytest.mark.asyncio
async def test_tool_loop_skipped_when_premium_cooling(monkeypatch):
    """3.1: tool loop is the FIRST feature off when degraded."""
    monkeypatch.setattr(settings, "TOOL_LOOP_ENABLED", True)
    resilience._cool_down("premium-chat", 600)
    with patch(
        "services.ai.AIGateway.complete",
        new=AsyncMock(side_effect=AssertionError("must not call any model")),
    ):
        answer, model = await run_tool_loop("laptop nào tốt?", AsyncMock())
    assert (answer, model) == (None, None)


@pytest.mark.asyncio
async def test_tool_loop_hop_cap(monkeypatch):
    """G2: more tool rounds than TOOL_LOOP_MAX_HOPS → bail to normal path."""
    monkeypatch.setattr(settings, "TOOL_LOOP_ENABLED", True)
    tc = SimpleNamespace(
        id="tc1",
        function=SimpleNamespace(name="check_inventory", arguments='{"sku": "L1"}'),
    )
    inventory_ok = MagicMock(
        success=True, data=SimpleNamespace(sku="L1", stock_level=3), error=None
    )
    with (
        patch.object(resilience, "premium_budget_exhausted", new=AsyncMock(return_value=False)),
        patch(
            "services.ai.AIGateway.complete",
            new=AsyncMock(return_value=_llm_response("", tool_calls=[tc])),
        ) as comp,
        patch(
            "core.agent.tools.execute_inventory_lookup",
            new=AsyncMock(return_value=inventory_ok),
        ),
    ):
        answer, model = await run_tool_loop("còn hàng không?", AsyncMock())
    assert (answer, model) == (None, None)
    assert comp.await_count <= settings.TOOL_LOOP_MAX_CALLS
