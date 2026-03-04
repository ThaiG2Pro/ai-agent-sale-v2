"""Integration tests for agent flow (T061, T071, T077-T079, T083-T085).

Tests full graph execution paths with mocked LLM and RAG calls.

Note on patching litellm: Both router_node and answer_node call `litellm.acompletion`
on the same shared module object. Patching them separately at different namespaces
results in the last patch winning for all calls. Solution: patch `litellm.acompletion`
once with side_effect=[router_response, answer_response] to control call order.
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


def _make_rag_output(similarity_score: float, declined: bool):
    """Build a mock RAGSearchOutput."""
    from core.agent.tools import RAGSearchOutput

    return RAGSearchOutput(
        answer="some answer",
        citations=[],
        similarity_score=similarity_score,
        confidence_score=similarity_score,
        rerank_score=None,
        chunks_used=1,
        model_used="economy-chat",
        declined=declined,
    )


def _mock_rag_tool(similarity_score: float, declined: bool):
    """Build mock rag tool that returns given output."""
    rag_output = _make_rag_output(similarity_score, declined)
    mock_tool = MagicMock()
    mock_tool.ainvoke = AsyncMock(return_value=rag_output)
    return mock_tool


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
    """T071: COMPLAINT intent routes to escalation_node → premium model used (SC-002).

    LLM call order: 1=router (COMPLAINT), 2=answer (with premium model).
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
    state = make_initial_state("Tôi muốn khiếu nại về đơn hàng", "test-complaint")

    with patch("litellm.acompletion", mock_llm):
        result = await graph.ainvoke(state, config)

    assert result["escalation_flag"] is True
    assert result["escalation_reason"] == "intent_escalation"
    assert result["model_used"] == "premium-chat"


# ---------------------------------------------------------------------------
# Phase 6: US3 — Low-Confidence Fallback (T077-T079)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_low_confidence_fallback(monkeypatch):
    """T077+T079: similarity=0.50 → Layer 2 fires, DECLINE_MESSAGE under 200ms.

    LLM call order: 1=router (INFO_QUERY). Answer LLM must NOT be called (declined path)
    """
    monkeypatch.setattr("core.agent.nodes.escalation.settings.PREMIUM_MODEL", "premium-chat")

    mock_llm = AsyncMock(
        side_effect=[
            _make_router_response("INFO_QUERY"),
            # answer_node should NOT be called → if it is, we'll get StopIteration
        ],
    )
    mock_tool = _mock_rag_tool(similarity_score=0.50, declined=False)

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "test-low-conf"}}
    state = make_initial_state("Sản phẩm gì tốt nhất?", "test-low-conf")

    start = time.perf_counter()
    with patch("litellm.acompletion", mock_llm):
        with patch("core.agent.nodes.retrieval.make_rag_tool", return_value=mock_tool):
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
    mock_tool = _mock_rag_tool(similarity_score=0.40, declined=True)  # Layer 1 fires

    graph = build_graph(checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "test-layer1"}}
    state = make_initial_state("Random unknown query", "test-layer1")

    with patch("litellm.acompletion", mock_llm):
        with patch("core.agent.nodes.retrieval.make_rag_tool", return_value=mock_tool):
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
    mock_tool = _mock_rag_tool(similarity_score=0.85, declined=False)

    events = []
    with patch("litellm.acompletion", mock_llm):
        with patch("core.agent.nodes.retrieval.make_rag_tool", return_value=mock_tool):
            async for event in astream_agent(
                "Còn hàng không?",
                "stream-test-1",
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
    with patch("litellm.acompletion", mock_llm):
        async for event in astream_agent("Xin chào!", "stream-test-2", checkpointer=MemorySaver()):
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
    mock_tool = _mock_rag_tool(similarity_score=0.85, declined=False)

    events = []
    with patch("litellm.acompletion", mock_llm):
        with patch("core.agent.nodes.retrieval.make_rag_tool", return_value=mock_tool):
            async for event in astream_agent(
                "Giá sản phẩm X?",
                "stream-test-3",
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
