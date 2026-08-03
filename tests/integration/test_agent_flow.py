"""Integration tests for agent flow (T061, T071, T077-T079, T083-T085).

Tests full graph execution paths with mocked LLM and RAG calls.

Note on patching: Both router_node and answer_node call AIGateway.complete()
which delegates to services.ai.ai_router.acompletion (LiteLLM Router).
Patch "services.ai.ai_router.acompletion" once with side_effect=[router_response,
answer_response] to control call order across all nodes.
"""

from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.checkpoint.memory import MemorySaver

from core.agent.graph import astream_agent, build_graph
from core.agent.state import NodeStreamEvent, make_initial_state
from services.rag.constants import DECLINE_MESSAGE

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_router_response(intent: str, confidence: float = 0.9):
    """Make mock LiteLLM response for router_node."""
    import json

    content = json.dumps(
        {
            "primary_intent": intent,
            "secondary_intents": [],
            "confidence": confidence,
            "reasoning": "test reasoning",
        },
    )
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_answer_response(text: str = "Test answer"):
    """Make mock LiteLLM response for answer_node."""
    choice = MagicMock()
    choice.message.content = text
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_retrieval_result(
    similarity_score: float, declined: bool, cached_answer: str | None = None
):
    """Build a mock RetrievalResult for patching search_and_retrieve."""
    from services.rag.pipeline import RetrievalResult

    return RetrievalResult(
        cached_answer=cached_answer,
        cached_citations=[],
        declined=declined,
        citations=[],
        chunks=[],
        best_similarity=similarity_score,
        similarity_gap=0.0,
        canonical_query="test query",
        query_vector=[0.1] * 10,
        query_category="INFO_QUERY",
        top_k_used=5,
    )


def _mock_search_and_retrieve(
    similarity_score: float, declined: bool, cached_answer: str | None = None
):
    """Build mock for make_retrieval_tool that returns given RetrievalResult.

    Returns a factory (db) -> @tool mock with .ainvoke(), matching make_retrieval_tool signature.
    Patch target: core.agent.nodes.retrieval.make_retrieval_tool
    """
    result = _make_retrieval_result(similarity_score, declined, cached_answer)
    mock_tool = MagicMock()
    mock_tool.ainvoke = AsyncMock(return_value=result)

    def mock_factory(db):
        return mock_tool

    return mock_factory


# ---------------------------------------------------------------------------
# Graph structure tests (T061)
# ---------------------------------------------------------------------------


def test_graph_structure():
    """Test that build_graph() returns a valid compiled graph."""
    graph = build_graph()
    assert graph is not None
    assert hasattr(graph, "ainvoke")
    assert hasattr(graph, "invoke")


def test_mermaid_diagram_generation():
    """Test that mermaid diagram can be generated."""
    from core.agent.graph import get_mermaid_diagram

    diagram = get_mermaid_diagram()
    assert "router_node" in diagram
    assert "retrieval_node" in diagram
    assert "confidence_node" in diagram
    assert "answer_node" in diagram
    assert "escalation_node" in diagram


def test_graph_with_checkpointer():
    """Test that graph can be created with a checkpointer."""
    checkpointer = MemorySaver()
    graph = build_graph(checkpointer=checkpointer)
    assert graph is not None
    assert hasattr(graph, "ainvoke")


# ---------------------------------------------------------------------------
# Phase 5: US2 — Complaint Escalation (T071)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complaint_escalation_flow(monkeypatch):
    """T071 + WP-V2-1: COMPLAINT routes to escalation_node → cascade verification.

    Since WP-V2-1, intent escalations answer on economy-chat FIRST; PREMIUM_MODEL is
    reserved and only spent when the groundedness verdict fails. The escalation flag
    still carries the intent_escalation reason.
    LLM call order: 1=router (COMPLAINT), 2=answer (economy first pass, accepted).
    """
    monkeypatch.setattr("core.agent.nodes.escalation.settings.PREMIUM_MODEL", "premium-chat")

    mock_llm = AsyncMock(
        side_effect=[
            _make_router_response("COMPLAINT"),
            _make_answer_response("I understand your complaint."),
        ],
    )

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "test-complaint"}}
    state = make_initial_state("Tôi muốn khiếu nại về đơn hàng", "test-complaint", "cust_test")

    with patch("services.ai.ai_router.acompletion", mock_llm):
        result = await graph.ainvoke(state, config)

    assert result["escalation_flag"] is True
    assert result["escalation_reason"] == "intent_escalation"
    # WP-V2-1 cascade: economy first pass accepted → premium never spent this turn
    assert result["model_used"] == "economy-chat"


# ---------------------------------------------------------------------------
# Phase 6: US3 — Low-Confidence Fallback (T077-T079)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_low_confidence_fallback(monkeypatch):
    """T077+T079: COMPARISON similarity=0.50 → Layer 2 fires, DECLINE_MESSAGE under 200ms.

    Uses COMPARISON intent (not INFO_QUERY) since INFO_QUERY borderline now escalates.
    LLM call order: 1=router (COMPARISON). Answer LLM must NOT be called (declined path).
    """
    monkeypatch.setattr("core.agent.nodes.escalation.settings.PREMIUM_MODEL", "premium-chat")

    mock_llm = AsyncMock(
        side_effect=[
            _make_router_response("COMPARISON"),
            # answer_node should NOT be called → if it is, we'll get StopIteration
        ],
    )
    mock_retrieval = _mock_search_and_retrieve(similarity_score=0.50, declined=False)

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "test-low-conf"}}
    state = make_initial_state("Sản phẩm gì tốt nhất?", "test-low-conf", "cust_test")

    start = time.perf_counter()
    with patch("services.ai.ai_router.acompletion", mock_llm):
        with patch("core.agent.nodes.retrieval.make_retrieval_tool", mock_retrieval):
            result = await graph.ainvoke(state, config)
    elapsed = time.perf_counter() - start

    assert result["response"] == DECLINE_MESSAGE
    assert result["model_used"] is None
    # Only router should have been called (1 call)
    assert mock_llm.call_count == 1
    # T079: performance assertion
    assert elapsed < 0.2, f"Fallback took {elapsed:.3f}s, expected < 0.2s"


@pytest.mark.asyncio
async def test_layer1_declined_propagation(monkeypatch):
    """T078: Layer 1 declined=True propagates through confidence_node fast-path."""
    monkeypatch.setattr("core.agent.nodes.escalation.settings.PREMIUM_MODEL", "premium-chat")

    mock_llm = AsyncMock(
        side_effect=[
            _make_router_response("INFO_QUERY"),
            # answer_node must NOT be called
        ],
    )
    mock_retrieval = _mock_search_and_retrieve(
        similarity_score=0.40, declined=True
    )  # Layer 1 fires

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "test-layer1"}}
    state = make_initial_state("Random unknown query", "test-layer1", "cust_test")

    with patch("services.ai.ai_router.acompletion", mock_llm):
        with patch("core.agent.nodes.retrieval.make_retrieval_tool", mock_retrieval):
            result = await graph.ainvoke(state, config)

    assert result["response"] == DECLINE_MESSAGE
    assert result["model_used"] is None
    assert mock_llm.call_count == 1  # only router called


# ---------------------------------------------------------------------------
# Phase 7: US4 — Streaming (T083-T085)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_streaming_emits_events(monkeypatch):
    """T083: astream_agent emits ≥ 4 NodeStreamEvents covering key nodes."""
    monkeypatch.setattr("core.agent.nodes.escalation.settings.PREMIUM_MODEL", "premium-chat")

    mock_llm = AsyncMock(
        side_effect=[
            _make_router_response("INFO_QUERY"),
            _make_answer_response("Found relevant products."),
        ],
    )
    mock_retrieval = _mock_search_and_retrieve(similarity_score=0.85, declined=False)

    events = []
    with patch("services.ai.ai_router.acompletion", mock_llm):
        with patch("core.agent.nodes.retrieval.make_retrieval_tool", mock_retrieval):
            async for event in astream_agent(
                "Còn hàng không?",
                "stream-test-1",
                "cust_test",
                checkpointer=MemorySaver(),
            ):
                events.append(event)

    assert len(events) >= 4, (
        f"Expected ≥4 events, got {len(events)}: {[e.node_name for e in events]}"
    )
    node_names = {e.node_name for e in events}
    assert "router_node" in node_names
    assert "retrieval_node" in node_names or "escalation_node" in node_names
    assert "answer_node" in node_names


@pytest.mark.asyncio
async def test_streaming_events_have_required_fields(monkeypatch):
    """T084: Each NodeStreamEvent has node_name, state_snapshot, valid ISO timestamp."""
    monkeypatch.setattr("core.agent.nodes.escalation.settings.PREMIUM_MODEL", "premium-chat")

    mock_llm = AsyncMock(
        side_effect=[
            _make_router_response("SMALLTALK", confidence=0.95),
            _make_answer_response("Hello!"),
        ],
    )

    events = []
    with patch("services.ai.ai_router.acompletion", mock_llm):
        async for event in astream_agent(
            "Xin chào!", "stream-test-2", "cust_test", checkpointer=MemorySaver()
        ):
            events.append(event)

    assert len(events) > 0, "Expected at least one streaming event"
    for event in events:
        assert isinstance(event, NodeStreamEvent)
        assert event.node_name
        assert event.state_snapshot is not None
        assert event.timestamp
        datetime.fromisoformat(event.timestamp)  # raises if invalid ISO


@pytest.mark.asyncio
async def test_streaming_execution_replay(monkeypatch):
    """T085: Events in arrival order reconstruct correct execution path."""
    monkeypatch.setattr("core.agent.nodes.escalation.settings.PREMIUM_MODEL", "premium-chat")

    mock_llm = AsyncMock(
        side_effect=[
            _make_router_response("INFO_QUERY"),
            _make_answer_response("Product details here."),
        ],
    )
    mock_retrieval = _mock_search_and_retrieve(similarity_score=0.85, declined=False)

    events = []
    with patch("services.ai.ai_router.acompletion", mock_llm):
        with patch("core.agent.nodes.retrieval.make_retrieval_tool", mock_retrieval):
            async for event in astream_agent(
                "Giá sản phẩm X?",
                "stream-test-3",
                "cust_test",
                checkpointer=MemorySaver(),
            ):
                events.append(event)

    assert len(events) >= 4
    node_names = [e.node_name for e in events]
    # router_node first, answer_node last
    assert node_names[0] == "router_node"
    assert node_names[-1] == "answer_node"
    # retrieval before confidence when present
    if "retrieval_node" in node_names and "confidence_node" in node_names:
        assert node_names.index("retrieval_node") < node_names.index("confidence_node")
