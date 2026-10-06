"""Memory recall regression tests — vague follow-ups that name no product.

Covers: citations no longer accumulating across turns (stale "nó" resolution),
referential query detection, resolving a vague query from customer memory BEFORE
the catalog search, and the structured recall digest in memory_context.
"""

import operator
from types import SimpleNamespace
from typing import get_type_hints
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from core.agent.nodes.memory_retrieval import memory_retrieval_node
from core.agent.nodes.retrieval import _expand_pronoun_query, retrieval_node
from core.agent.state import AgentState, make_initial_state
from services.memory.recall import is_referential, recall_digest


def _retrieval_result(declined: bool, names: list[str]):
    citations = [
        {
            "product_id": f"pid-{i}",
            "chunk_id": f"chunk-{i}",
            "sku": f"SKU-{i}",
            "name": n,
            "source_text": f"Thông tin về {n}.",
        }
        for i, n in enumerate(names)
    ]
    return SimpleNamespace(
        declined=declined,
        citations=citations,
        best_similarity=0.9 if not declined else 0.2,
        cached_answer=None,
        canonical_query="canonical",
        query_vector=None,
    )


def _config(db=None):
    return {"configurable": {"thread_id": "s1", "db": db or AsyncMock()}}


def _state(msg: str, **overrides):
    state = make_initial_state(msg, "s1", "cust_001")
    state["intent"] = "PRICING"
    state.update(overrides)
    return state


def _fake_tool(names: list[str], declined: bool = False):
    tool = MagicMock()
    tool.ainvoke = AsyncMock(return_value=_retrieval_result(declined, names))
    return tool


# ── citations are per-turn ───────────────────────────────────────────────


class TestCitationsPerTurn:
    def test_citations_channel_has_no_accumulating_reducer(self):
        hint = get_type_hints(AgentState, include_extras=True)["citations"]
        assert operator.add not in getattr(hint, "__metadata__", ())

    def test_checkpointed_turns_do_not_accumulate(self):
        """Turn 3 sees only its own citations, not turn 1's + turn 2's."""

        def node(state):
            return {"citations": [state["user_message"]]}

        builder = StateGraph(AgentState)
        builder.add_node("n", node)
        builder.add_edge(START, "n")
        builder.add_edge("n", END)
        graph = builder.compile(checkpointer=MemorySaver())
        cfg = {"configurable": {"thread_id": "t"}}
        for msg in ("A", "B", "C"):
            out = graph.invoke(make_initial_state(msg, "t", "cust"), cfg)
        assert out["citations"] == ["C"]

    def test_pronoun_resolves_to_latest_products_not_oldest(self):
        state = {
            "recent_products": ["Galaxy S24"],
            "citations": [{"name": "iPhone 15"}],
        }
        assert _expand_pronoun_query("nó giá bao nhiêu", state) == "Galaxy S24 giá bao nhiêu"


# ── referential detection ────────────────────────────────────────────────


class TestIsReferential:
    @pytest.mark.parametrize(
        "query",
        [
            "con đó giá bao nhiêu",
            "máy này còn hàng không",
            "chiếc kia pin trâu không",
            "cái máy hôm qua em tư vấn ấy",
            "mẫu em vừa tư vấn có màu đen không",
            "lần trước anh hỏi con laptop gì nhỉ",
        ],
    )
    def test_referential(self, query):
        assert is_referential(query)

    @pytest.mark.parametrize("query", ["giá Dell XPS 15", "laptop gaming dưới 30 triệu", "", None])
    def test_not_referential(self, query):
        assert not is_referential(query)


# ── retrieval_node resolves vague queries from memory ────────────────────


class TestRetrievalRecall:
    @pytest.mark.asyncio
    async def test_new_session_vague_query_resolved_from_memory(self):
        tool = _fake_tool(["Dell XPS 15"])
        with (
            patch("core.agent.nodes.retrieval.make_retrieval_tool", return_value=tool),
            patch(
                "services.memory.recall.recall_products",
                new=AsyncMock(return_value=["Dell XPS 15"]),
            ),
        ):
            result = await retrieval_node(_state("con đó giá bao nhiêu"), _config())

        sent = tool.ainvoke.await_args.args[0]["query"]
        assert sent == "Dell XPS 15 con đó giá bao nhiêu"
        assert result["resolved_query"] == sent
        assert result["recent_products"] == ["Dell XPS 15"]

    @pytest.mark.asyncio
    async def test_thread_products_win_for_plain_deictic(self):
        tool = _fake_tool(["Galaxy S24"])
        recall = AsyncMock(return_value=["Dell XPS 15"])
        with (
            patch("core.agent.nodes.retrieval.make_retrieval_tool", return_value=tool),
            patch("services.memory.recall.recall_products", new=recall),
        ):
            await retrieval_node(
                _state("con đó còn hàng không", recent_products=["Galaxy S24"]), _config()
            )

        assert tool.ainvoke.await_args.args[0]["query"].startswith("Galaxy S24 ")
        recall.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_time_reference_prefers_earlier_threads(self):
        tool = _fake_tool(["Dell XPS 15"])
        recall = AsyncMock(return_value=["Dell XPS 15"])
        with (
            patch("core.agent.nodes.retrieval.make_retrieval_tool", return_value=tool),
            patch("services.memory.recall.recall_products", new=recall),
        ):
            await retrieval_node(
                _state("cái máy hôm qua em tư vấn ấy giá sao", recent_products=["Galaxy S24"]),
                _config(),
            )

        assert tool.ainvoke.await_args.args[0]["query"].startswith("Dell XPS 15 ")
        assert recall.await_args.kwargs["exclude_thread_id"] == "s1"

    @pytest.mark.asyncio
    async def test_specific_query_untouched(self):
        tool = _fake_tool(["Dell XPS 15"])
        recall = AsyncMock(return_value=["iPhone 15"])
        with (
            patch("core.agent.nodes.retrieval.make_retrieval_tool", return_value=tool),
            patch("services.memory.recall.recall_products", new=recall),
        ):
            result = await retrieval_node(_state("giá Dell XPS 15"), _config())

        assert tool.ainvoke.await_args.args[0]["query"] == "giá Dell XPS 15"
        assert "resolved_query" not in result
        recall.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_kill_switch_keeps_literal_query(self):
        tool = _fake_tool([])
        with (
            patch("core.agent.nodes.retrieval.make_retrieval_tool", return_value=tool),
            patch(
                "services.memory.recall.recall_products",
                new=AsyncMock(return_value=["Dell XPS 15"]),
            ),
            patch("core.agent.nodes.retrieval.settings.MEMORY_RECALL_ENABLED", False),
        ):
            await retrieval_node(_state("con đó giá bao nhiêu"), _config())

        assert tool.ainvoke.await_args.args[0]["query"] == "con đó giá bao nhiêu"


# ── memory_retrieval_node: resolved query + digest ───────────────────────


class TestMemoryNodeRecall:
    @pytest.mark.asyncio
    async def test_uses_resolved_query_and_prepends_digest(self):
        service = AsyncMock()
        service.retrieve = AsyncMock(return_value=[])
        state = _state("con đó giá bao nhiêu", resolved_query="Dell XPS 15 con đó giá bao nhiêu")
        with (
            patch("services.memory.semantic_memory.SemanticMemoryService", return_value=service),
            patch(
                "services.memory.episodic.EpisodicMemoryService.recent_events",
                new=AsyncMock(return_value=[]),
            ),
            patch(
                "services.memory.recall.recall_digest",
                new=AsyncMock(return_value="Sản phẩm khách đã quan tâm: Dell XPS 15"),
            ),
        ):
            result = await memory_retrieval_node(state, _config())

        assert service.retrieve.await_args.kwargs["query"] == "Dell XPS 15 con đó giá bao nhiêu"
        assert result["memory_context"][0]["source"] == "recall_digest"
        assert len(result["memory_context"]) == len(result["memory_retrieval_scores"])

    @pytest.mark.asyncio
    async def test_non_referential_query_gets_no_digest(self):
        service = AsyncMock()
        service.retrieve = AsyncMock(return_value=[])
        digest = AsyncMock(return_value="x")
        with (
            patch("services.memory.semantic_memory.SemanticMemoryService", return_value=service),
            patch("services.memory.recall.recall_digest", new=digest),
        ):
            result = await memory_retrieval_node(_state("giá Dell XPS 15"), _config())

        digest.assert_not_awaited()
        assert result["memory_context"] == []


# ── recall_digest formatting ─────────────────────────────────────────────


class TestRecallDigest:
    @pytest.mark.asyncio
    async def test_digest_merges_structured_summary_fields(self):
        rows = [
            SimpleNamespace(
                products_discussed=["Dell XPS 15", "MacBook Air M3"],
                open_questions=["Có trả góp không?"],
                budget_stated="30 triệu",
                customer_preference=None,
            ),
            SimpleNamespace(
                products_discussed=["Dell XPS 15", "Galaxy S24"],
                open_questions=[],
                budget_stated=None,
                customer_preference="màn hình đẹp",
            ),
        ]
        db = AsyncMock()
        result = MagicMock()
        result.scalars.return_value.all.return_value = rows
        db.execute = AsyncMock(return_value=result)

        digest = await recall_digest(customer_id="cust_001", db=db)

        assert "Dell XPS 15, MacBook Air M3, Galaxy S24" in digest
        assert "Ngân sách: 30 triệu" in digest
        assert "Sở thích: màn hình đẹp" in digest
        assert "Có trả góp không?" in digest

    @pytest.mark.asyncio
    async def test_digest_none_on_db_error(self):
        db = AsyncMock()
        db.execute = AsyncMock(side_effect=RuntimeError("db down"))
        assert await recall_digest(customer_id="cust_001", db=db) is None
