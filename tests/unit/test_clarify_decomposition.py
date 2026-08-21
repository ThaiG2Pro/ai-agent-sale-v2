"""Unit tests for WP-V2-3: clarify-question loop + LLM query decomposition.

Why: borderline queries must get ONE clarifying question instead of a decline
(max 1 per original query, CLARIFY_ENABLED kill switch), and declined
multi-intent queries must be LLM-decomposed with the COMPARISON regex split
kept as fallback.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agent.nodes.clarify import FALLBACK_CLARIFY_QUESTION, clarify_node
from core.agent.nodes.confidence import _route_after_confidence, confidence_node
from core.agent.nodes.retrieval import _decompose_query, retrieval_node
from core.agent.state import make_initial_state

# ── helpers ───────────────────────────────────────────────────────────────


def _llm(content: str):
    """Fake AIGateway.complete result with .choices[0].message.content."""
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _retrieval_result(declined: bool, similarity: float, skus: list[str] | None = None):
    """Fake RetrievalResult as returned by the retrieval tool."""
    skus = skus or []
    citations = [
        {
            "product_id": f"pid-{s}",
            "chunk_id": f"chunk-{s}",
            "sku": s,
            "name": f"Product {s}",
            "source_text": f"Thông tin về {s}.",
        }
        for s in skus
    ]
    return SimpleNamespace(
        declined=declined,
        citations=citations,
        best_similarity=similarity,
        cached_answer=None,
        canonical_query="canonical",
        query_vector=None,
    )


def _mock_config():
    return {"configurable": {"thread_id": "t", "db": AsyncMock()}}


def _state(msg: str = "câu hỏi mơ hồ", **overrides):
    state = make_initial_state(msg, "session-clarify", "cust_001")
    state.update(overrides)
    return state


# ── clarify gate in confidence_node ──────────────────────────────────────


class TestClarifyGate:
    @pytest.mark.asyncio
    async def test_borderline_first_pass_asks_clarification(self):
        state = _state(similarity_score=0.60, declined=False)
        result = await confidence_node(state, _mock_config())
        assert result["needs_clarification"] is True
        assert result["declined"] is False

    @pytest.mark.asyncio
    async def test_kill_switch_restores_old_decline(self, monkeypatch):
        monkeypatch.setattr("core.agent.nodes.confidence.settings.CLARIFY_ENABLED", False)
        state = _state(similarity_score=0.60, declined=False)
        result = await confidence_node(state, _mock_config())
        assert result["needs_clarification"] is False
        assert result["declined"] is True

    @pytest.mark.asyncio
    async def test_second_clarify_declines(self):
        """Anti-loop: quota (v3-0 P2: CLARIFY_MAX_ROUNDS=2) spent and still
        borderline with no citations → decline, no further question."""
        state = _state(similarity_score=0.60, declined=False, clarify_count=2)
        result = await confidence_node(state, _mock_config())
        assert result["needs_clarification"] is False
        assert result["declined"] is True

    @pytest.mark.asyncio
    async def test_layer1_decline_never_clarifies(self):
        """Out-of-catalog (Layer 1) is not a vagueness problem — decline as before."""
        state = _state(similarity_score=0.30, declined=True)
        result = await confidence_node(state, _mock_config())
        assert result["declined"] is True
        assert not result.get("needs_clarification")

    @pytest.mark.asyncio
    async def test_order_placement_keeps_hitl_path(self):
        state = _state("đặt hàng", intent="ORDER_PLACEMENT", similarity_score=0.60, declined=False)
        result = await confidence_node(state, _mock_config())
        assert result["needs_clarification"] is False
        state.update(result)
        assert _route_after_confidence(state) == "hitl_guard_node"

    @pytest.mark.asyncio
    async def test_memory_context_override_wins_over_clarify(self):
        """Peer behavior preserved: memory context clears the decline entirely."""
        state = _state(
            similarity_score=0.60,
            declined=False,
            memory_context=[{"summary_text": "khách đã hỏi về Dell XPS"}],
        )
        result = await confidence_node(state, _mock_config())
        assert result["declined"] is False
        assert result["needs_clarification"] is False

    @pytest.mark.asyncio
    async def test_borderline_answer_intents_keep_escalation_path(self):
        """PRICING and large-gap INFO_QUERY keep escalation path;
        small-gap INFO_QUERY clarifies (WP-V3-4).
        """
        # PRICING keeps escalation path
        state_pricing = _state(
            intent="PRICING", similarity_score=0.60, similarity_gap=0.01, declined=False
        )
        result_pricing = await confidence_node(state_pricing, _mock_config())
        assert result_pricing["needs_clarification"] is False
        assert result_pricing["declined"] is False
        state_pricing.update(result_pricing)
        assert _route_after_confidence(state_pricing) == "escalation_node"

        # INFO_QUERY with large gap (> 0.05) keeps escalation path
        state_info_large = _state(
            intent="INFO_QUERY", similarity_score=0.60, similarity_gap=0.10, declined=False
        )
        result_info_large = await confidence_node(state_info_large, _mock_config())
        assert result_info_large["needs_clarification"] is False
        assert result_info_large["declined"] is False
        state_info_large.update(result_info_large)
        assert _route_after_confidence(state_info_large) == "escalation_node"

        # INFO_QUERY with small gap (<= 0.05) clarifies (WP-V3-4)
        state_info_small = _state(
            intent="INFO_QUERY", similarity_score=0.60, similarity_gap=0.01, declined=False
        )
        result_info_small = await confidence_node(state_info_small, _mock_config())
        assert result_info_small["needs_clarification"] is True
        assert result_info_small["declined"] is False
        state_info_small.update(result_info_small)
        assert _route_after_confidence(state_info_small) == "clarify_node"

    def test_route_needs_clarification_to_clarify_node(self):
        state = _state(similarity_score=0.60, needs_clarification=True, declined=False)
        assert _route_after_confidence(state) == "clarify_node"


# ── clarify_node ──────────────────────────────────────────────────────────


class TestClarifyNode:
    @pytest.mark.asyncio
    async def test_generates_question_and_sets_flags(self):
        state = _state("màn hình cong có tốt không")
        state["citations"] = [
            {
                "product_id": "p1",
                "chunk_id": "c1",
                "sku": "MON-1",
                "name": "LG UltraGear 34",
                "source_text": "x",
            },
            {
                "product_id": "p2",
                "chunk_id": "c2",
                "sku": "MON-2",
                "name": "Samsung Odyssey G9",
                "source_text": "y",
            },
        ]
        question = "Anh/chị đang hỏi về LG UltraGear 34 hay Samsung Odyssey G9 ạ?"
        with patch(
            "core.agent.nodes.clarify.AIGateway.complete",
            new=AsyncMock(return_value=_llm(f'{{"question": "{question}"}}')),
        ) as mock_llm:
            result = await clarify_node(state, _mock_config())

        assert result["response"] == question
        assert result["awaiting_clarification"] is True
        assert result["clarify_original_query"] == "màn hình cong có tốt không"
        assert result["clarify_count"] == 1
        assert result["model_used"] == "clarify"
        # Candidate product names reach the prompt
        system_msg = mock_llm.await_args.kwargs["messages"][0]["content"]
        assert "LG UltraGear 34" in system_msg
        assert "Samsung Odyssey G9" in system_msg

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_static_question(self):
        state = _state("câu hỏi mơ hồ")
        with patch(
            "core.agent.nodes.clarify.AIGateway.complete",
            new=AsyncMock(side_effect=RuntimeError("429")),
        ):
            result = await clarify_node(state, _mock_config())

        assert result["response"] == FALLBACK_CLARIFY_QUESTION
        assert result["awaiting_clarification"] is True
        assert result["clarify_count"] == 1


# ── clarify-reply merge in retrieval_node ────────────────────────────────


class TestClarifyMerge:
    @pytest.mark.asyncio
    async def test_reply_turn_merges_original_query(self):
        state = _state(
            "Dell XPS 15",
            awaiting_clarification=True,
            clarify_original_query="cấu hình có mạnh không",
        )
        fake_tool = MagicMock()
        fake_tool.ainvoke = AsyncMock(return_value=_retrieval_result(False, 0.9, ["LAPTOP-1"]))
        with patch("core.agent.nodes.retrieval.make_retrieval_tool", return_value=fake_tool):
            result = await retrieval_node(state, _mock_config())

        sent_query = fake_tool.ainvoke.await_args.args[0]["query"]
        assert sent_query == "cấu hình có mạnh không Dell XPS 15"
        assert result["awaiting_clarification"] is False
        assert result["clarify_original_query"] is None

    @pytest.mark.asyncio
    async def test_fresh_query_resets_clarify_budget(self):
        state = _state("iPhone 15 giá bao nhiêu", clarify_count=1)
        fake_tool = MagicMock()
        fake_tool.ainvoke = AsyncMock(return_value=_retrieval_result(False, 0.9, ["PHONE-1"]))
        with patch("core.agent.nodes.retrieval.make_retrieval_tool", return_value=fake_tool):
            result = await retrieval_node(state, _mock_config())

        assert result["clarify_count"] == 0


# ── LLM query decomposition ──────────────────────────────────────────────


class TestDecomposition:
    @pytest.mark.asyncio
    async def test_decompose_query_caps_at_three(self):
        subs = '{"sub_queries": ["q1", "q2", "q3", "q4"]}'
        with patch("services.ai.AIGateway.complete", new=AsyncMock(return_value=_llm(subs))):
            parts = await _decompose_query("q")
        assert parts == ["q1", "q2", "q3"]

    @pytest.mark.asyncio
    async def test_decompose_single_intent_returns_none(self):
        with patch(
            "services.ai.AIGateway.complete",
            new=AsyncMock(return_value=_llm('{"sub_queries": ["chỉ một ý"]}')),
        ):
            assert await _decompose_query("q") is None

    @pytest.mark.asyncio
    async def test_decompose_llm_error_returns_none(self):
        with patch(
            "services.ai.AIGateway.complete", new=AsyncMock(side_effect=RuntimeError("offline"))
        ):
            assert await _decompose_query("q") is None

    @pytest.mark.asyncio
    async def test_declined_multi_intent_merges_subquery_results(self):
        """PRICING (not just COMPARISON) declined → LLM decomposition rescues it."""
        state = _state("Giá Galaxy A55 và còn hàng không?", intent="PRICING")
        fake_tool = MagicMock()
        fake_tool.ainvoke = AsyncMock(return_value=_retrieval_result(True, 0.3))

        sub_results = [
            SimpleNamespace(
                declined=False,
                citations=_retrieval_result(False, 0.8, ["PHONE-SM-002"]).citations,
                best_similarity=0.8,
                canonical_query="giá galaxy a55",
                query_vector=[0.1],
            ),
            SimpleNamespace(
                declined=False,
                citations=_retrieval_result(False, 0.75, ["PHONE-SM-002"]).citations,
                best_similarity=0.75,
                canonical_query="galaxy a55 còn hàng",
                query_vector=[0.2],
            ),
        ]
        with (
            patch("core.agent.nodes.retrieval.make_retrieval_tool", return_value=fake_tool),
            patch(
                "core.agent.nodes.retrieval._decompose_query",
                new=AsyncMock(return_value=["Giá Galaxy A55", "Galaxy A55 còn hàng không"]),
            ),
            patch(
                "services.rag.pipeline.search_and_retrieve",
                new=AsyncMock(side_effect=sub_results),
            ) as mock_search,
        ):
            result = await retrieval_node(state, _mock_config())

        assert result["declined"] is False
        assert result["similarity_score"] == 0.8
        assert len(result["citations"]) == 1  # deduped by chunk_id
        assert mock_search.await_count == 2

    @pytest.mark.asyncio
    async def test_llm_error_falls_back_to_regex_for_comparison(self):
        state = _state("So sánh iPhone 15 và Galaxy S24", intent="COMPARISON")
        fake_tool = MagicMock()
        fake_tool.ainvoke = AsyncMock(return_value=_retrieval_result(True, 0.3))
        sub = SimpleNamespace(
            declined=False,
            citations=_retrieval_result(False, 0.8, ["PHONE-IP-001"]).citations,
            best_similarity=0.8,
            canonical_query="iphone 15",
            query_vector=[0.1],
        )
        with (
            patch("core.agent.nodes.retrieval.make_retrieval_tool", return_value=fake_tool),
            patch(
                "core.agent.nodes.retrieval._decompose_query",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "services.rag.pipeline.search_and_retrieve", new=AsyncMock(return_value=sub)
            ) as mock_search,
        ):
            result = await retrieval_node(state, _mock_config())

        # Regex split "So sánh iPhone 15 | Galaxy S24" still rescues the query
        assert mock_search.await_count == 2
        assert result["declined"] is False

    @pytest.mark.asyncio
    async def test_llm_error_non_comparison_stays_declined(self):
        state = _state("Giá Galaxy A55 và còn hàng không?", intent="PRICING")
        fake_tool = MagicMock()
        fake_tool.ainvoke = AsyncMock(return_value=_retrieval_result(True, 0.3))
        with (
            patch("core.agent.nodes.retrieval.make_retrieval_tool", return_value=fake_tool),
            patch(
                "core.agent.nodes.retrieval._decompose_query",
                new=AsyncMock(return_value=None),
            ),
            patch("services.rag.pipeline.search_and_retrieve", new=AsyncMock()) as mock_search,
        ):
            result = await retrieval_node(state, _mock_config())

        assert result["declined"] is True
        mock_search.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_kill_switch_skips_llm_keeps_regex(self, monkeypatch):
        monkeypatch.setattr(
            "core.agent.nodes.retrieval.settings.QUERY_DECOMPOSITION_ENABLED", False
        )
        state = _state("So sánh iPhone 15 và Galaxy S24", intent="COMPARISON")
        fake_tool = MagicMock()
        fake_tool.ainvoke = AsyncMock(return_value=_retrieval_result(True, 0.3))
        sub = SimpleNamespace(
            declined=False,
            citations=_retrieval_result(False, 0.8, ["PHONE-IP-001"]).citations,
            best_similarity=0.8,
            canonical_query="iphone 15",
            query_vector=[0.1],
        )
        with (
            patch("core.agent.nodes.retrieval.make_retrieval_tool", return_value=fake_tool),
            patch(
                "core.agent.nodes.retrieval._decompose_query", new=AsyncMock()
            ) as mock_decompose,
            patch("services.rag.pipeline.search_and_retrieve", new=AsyncMock(return_value=sub)),
        ):
            result = await retrieval_node(state, _mock_config())

        mock_decompose.assert_not_awaited()
        assert result["declined"] is False


class TestOrdinalExpansion:
    def test_expand_ordinal_comparison_query(self):
        from core.agent.nodes.retrieval import _expand_pronoun_query

        state = {
            "citations": [
                {"name": "ASUS VivoBook Pro 15 (2024)"},
                {"name": "Lenovo ThinkPad X1 Carbon Gen 12"},
            ]
        }
        res = _expand_pronoun_query("so sánh 1 và 2", state)
        assert res == "so sánh ASUS VivoBook Pro 15 (2024) và Lenovo ThinkPad X1 Carbon Gen 12"

    def test_expand_ordinal_single_query(self):
        from core.agent.nodes.retrieval import _expand_pronoun_query

        state = {"citations": [{"name": "ASUS VivoBook Pro 15 (2024)"}]}
        res = _expand_pronoun_query("lap 1", state)
        assert res == "ASUS VivoBook Pro 15 (2024)"

    def test_expand_ordinal_prefix_query(self):
        from core.agent.nodes.retrieval import _expand_pronoun_query

        state = {
            "citations": [
                {"name": "ASUS VivoBook Pro 15 (2024)"},
                {"name": "Lenovo ThinkPad X1 Carbon Gen 12"},
            ]
        }
        res = _expand_pronoun_query("cho tôi thông tin mẫu 2", state)
        assert res == "cho tôi thông tin Lenovo ThinkPad X1 Carbon Gen 12"
