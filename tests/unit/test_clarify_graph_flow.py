"""2-turn clarify-loop flow through the REAL compiled graph (WP-V2-3).

Why: unit tests on single nodes proved the pieces; this proves the wiring —
borderline turn 1 returns a clarifying question (not a decline), the state
persists in the checkpointer, and turn 2 merges the reply and answers.
All LLM calls are mocked (lesson: "test xanh, feature gãy" needs the real
graph, not a real model).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver

from core.agent.graph import build_graph, make_agent_config
from core.agent.state import make_initial_state
from services.rag.constants import DECLINE_MESSAGE

CLARIFY_QUESTION = "Anh/chị đang hỏi về Dell XPS 15 hay MacBook Pro 14 ạ?"
FINAL_ANSWER = "Dạ Dell XPS 15 có cấu hình rất mạnh ạ."


def _llm(content: str):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _fake_llm_dispatcher():
    """AIGateway.complete replacement — dispatches on response_format."""

    async def complete(*, model, messages, response_format=None, **kwargs):
        name = getattr(response_format, "__name__", None)
        if name == "IntentClassification":
            return _llm(
                '{"primary_intent": "OTHER", "secondary_intents": [], '
                '"confidence": 0.9, "reasoning": "test"}'
            )
        if name == "ClarifyingQuestion":
            return _llm(f'{{"question": "{CLARIFY_QUESTION}"}}')
        return _llm(FINAL_ANSWER)

    return AsyncMock(side_effect=complete)


def _retrieval_result(declined: bool, similarity: float, similarity_gap: float = 0.0):
    citations = (
        []
        if declined
        else [
            {
                "product_id": "p1",
                "chunk_id": f"chunk-{similarity}",
                "sku": "LAPTOP-DELL-001",
                "name": "Dell XPS 15",
                "source_text": "Dell XPS 15 dùng CPU Intel Core Ultra 7.",
            }
        ]
    )
    return SimpleNamespace(
        declined=declined,
        citations=citations,
        best_similarity=similarity,
        similarity_gap=similarity_gap,
        cached_answer=None,
        canonical_query="canonical",
        query_vector=None,
    )


@pytest.mark.asyncio
async def test_two_turn_clarify_flow(monkeypatch):
    """Turn 1 borderline → clarifying question; turn 2 reply → merged answer."""
    monkeypatch.setattr("core.config.settings.GROUNDEDNESS_CHECK_ENABLED", False)

    graph = build_graph(checkpointer=MemorySaver())
    config = make_agent_config("session-clarify-flow", db=AsyncMock())

    seen_queries: list[str] = []

    def make_fake_tool(_db):
        async def ainvoke(payload):
            seen_queries.append(payload["query"])
            # Turn 1 (original vague query) is borderline; merged turn 2 is strong
            return _retrieval_result(False, 0.9 if len(seen_queries) > 1 else 0.55)

        tool = MagicMock()
        tool.ainvoke = AsyncMock(side_effect=ainvoke)
        return tool

    patches = (
        patch("services.ai.AIGateway.complete", new=_fake_llm_dispatcher()),
        patch("core.agent.nodes.retrieval.make_retrieval_tool", side_effect=make_fake_tool),
        patch(
            "services.memory.semantic_memory.SemanticMemoryService.retrieve",
            new=AsyncMock(return_value=[]),
        ),
    )
    with patches[0], patches[1], patches[2]:
        # Turn 1: vague query → clarifying question, NOT a decline
        state1 = await graph.ainvoke(
            make_initial_state("cấu hình có mạnh không", "session-clarify-flow", "cust_9"),
            config,
        )
        assert state1["response"] == CLARIFY_QUESTION
        assert state1["response"] != DECLINE_MESSAGE
        assert state1["model_used"] == "clarify"

        checkpoint = await graph.aget_state(config)
        assert checkpoint.values["awaiting_clarification"] is True
        assert checkpoint.values["clarify_original_query"] == "cấu hình có mạnh không"
        assert checkpoint.values["clarify_count"] == 1

        # Turn 2: customer reply → merged retrieval → real answer
        state2 = await graph.ainvoke(
            make_initial_state("Dell XPS 15", "session-clarify-flow", "cust_9"),
            config,
        )
        assert seen_queries[1] == "cấu hình có mạnh không Dell XPS 15"
        assert state2["response"] == FINAL_ANSWER
        assert state2["awaiting_clarification"] is False

        checkpoint = await graph.aget_state(config)
        assert checkpoint.values["awaiting_clarification"] is False


@pytest.mark.asyncio
async def test_merged_turn_still_borderline_declines(monkeypatch):
    """Anti-loop: merged turn 2 still borderline → decline, no second question.

    Kill-switch regression: with ORDER_HITL_V3_ENABLED=False the clarify quota
    reverts to the pre-P2 single round and no handoff fires — the exact
    pre-v3-0 behavior this test always asserted.
    """
    monkeypatch.setattr("core.config.settings.GROUNDEDNESS_CHECK_ENABLED", False)
    monkeypatch.setattr("core.config.settings.ORDER_HITL_V3_ENABLED", False)

    graph = build_graph(checkpointer=MemorySaver())
    config = make_agent_config("session-clarify-loop2", db=AsyncMock())

    def make_fake_tool(_db):
        tool = MagicMock()
        tool.ainvoke = AsyncMock(return_value=_retrieval_result(False, 0.55))  # always borderline
        return tool

    patches = (
        patch("services.ai.AIGateway.complete", new=_fake_llm_dispatcher()),
        patch("core.agent.nodes.retrieval.make_retrieval_tool", side_effect=make_fake_tool),
        patch(
            "services.memory.semantic_memory.SemanticMemoryService.retrieve",
            new=AsyncMock(return_value=[]),
        ),
    )
    with patches[0], patches[1], patches[2]:
        state1 = await graph.ainvoke(
            make_initial_state("mơ hồ lần một", "session-clarify-loop2", "cust_9"), config
        )
        assert state1["response"] == CLARIFY_QUESTION

        state2 = await graph.ainvoke(
            make_initial_state("vẫn mơ hồ", "session-clarify-loop2", "cust_9"), config
        )
        assert state2["response"] == DECLINE_MESSAGE
        assert state2["declined"] is True


@pytest.mark.asyncio
async def test_two_turn_clarify_flow_info_query_borderline(monkeypatch):
    """WP-V3-4: INFO_QUERY borderline query with small similarity_gap triggers clarify turn 1."""
    monkeypatch.setattr("core.config.settings.GROUNDEDNESS_CHECK_ENABLED", False)

    graph = build_graph(checkpointer=MemorySaver())
    config = make_agent_config("session-clarify-info-query", db=AsyncMock())

    def _info_query_llm_dispatcher():
        async def complete(*, model, messages, response_format=None, **kwargs):
            name = getattr(response_format, "__name__", None)
            if name == "IntentClassification":
                return _llm(
                    '{"primary_intent": "INFO_QUERY", "secondary_intents": [], '
                    '"confidence": 0.9, "reasoning": "test"}'
                )
            if name == "ClarifyingQuestion":
                return _llm(f'{{"question": "{CLARIFY_QUESTION}"}}')
            return _llm(FINAL_ANSWER)

        return AsyncMock(side_effect=complete)

    seen_queries: list[str] = []

    def make_fake_tool(_db):
        async def ainvoke(payload):
            seen_queries.append(payload["query"])
            if len(seen_queries) == 1:
                # Turn 1: borderline INFO_QUERY with small gap (0.0019)
                return _retrieval_result(False, similarity=0.60, similarity_gap=0.0019)
            # Turn 2: merged query has strong similarity (0.90)
            return _retrieval_result(False, similarity=0.90, similarity_gap=0.15)

        tool = MagicMock()
        tool.ainvoke = AsyncMock(side_effect=ainvoke)
        return tool

    patches = (
        patch("services.ai.AIGateway.complete", new=_info_query_llm_dispatcher()),
        patch("core.agent.nodes.retrieval.make_retrieval_tool", side_effect=make_fake_tool),
        patch(
            "services.memory.semantic_memory.SemanticMemoryService.retrieve",
            new=AsyncMock(return_value=[]),
        ),
    )
    with patches[0], patches[1], patches[2]:
        # Turn 1: "Điện thoại Samsung ấy còn hàng không?" classified as INFO_QUERY with gap 0.0019
        state1 = await graph.ainvoke(
            make_initial_state(
                "Điện thoại Samsung ấy còn hàng không?", "session-clarify-info-query", "cust_9"
            ),
            config,
        )
        assert state1["response"] == CLARIFY_QUESTION
        assert state1["model_used"] == "clarify"

        checkpoint = await graph.aget_state(config)
        assert checkpoint.values["awaiting_clarification"] is True
        assert (
            checkpoint.values["clarify_original_query"] == "Điện thoại Samsung ấy còn hàng không?"
        )

        # Turn 2: customer specifies "S24 Ultra" → merged retrieval → answer
        state2 = await graph.ainvoke(
            make_initial_state("S24 Ultra", "session-clarify-info-query", "cust_9"),
            config,
        )
        assert seen_queries[1] == "Điện thoại Samsung ấy còn hàng không? S24 Ultra"
        assert state2["response"] == FINAL_ANSWER
        assert state2["awaiting_clarification"] is False
