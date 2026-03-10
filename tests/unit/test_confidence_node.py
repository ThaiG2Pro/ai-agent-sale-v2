"""Unit tests for confidence node (T058-T060)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agent.nodes.confidence import _route_after_confidence, confidence_node
from core.agent.state import (
    make_initial_state,
)
from core.config import settings


@pytest.fixture
def mock_config():
    """Minimal RunnableConfig with a mock DB that returns no product (non-ORDER tests)."""
    mock_db = AsyncMock()
    mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    return {"configurable": {"thread_id": "test", "db": mock_db}}


@pytest.mark.asyncio
async def test_confidence_node_fused_score_with_reranker(mock_config):
    """Test confidence_node fused score with reranker (T058).

    Given: similarity_score=0.8, rerank_score=0.9
    Expected: confidence_score ≈ (1-a)*0.8 + a*0.9 = 0.87
    where a=0.7 (AGENT_ALPHA): (1-0.7)*0.8 + 0.7*0.9 = 0.24 + 0.63 = 0.87
    """
    state = make_initial_state("Test query", "session-001")
    state["similarity_score"] = 0.8
    state["rerank_score"] = 0.9
    state["declined"] = False

    result = await confidence_node(state, mock_config)

    # Verify fused score: (1-0.7)*0.8 + 0.7*0.9 = 0.87
    expected_score = (1 - settings.AGENT_ALPHA) * 0.8 + settings.AGENT_ALPHA * 0.9
    assert pytest.approx(result["confidence_score"], rel=0.01) == expected_score
    assert result["declined"] is False  # High score, not declined


@pytest.mark.asyncio
async def test_confidence_node_fused_score_no_reranker(mock_config):
    """Test confidence_node without reranker (T059).

    Given: similarity_score=0.75, rerank_score=None
    Expected: confidence_score == 0.75 (fallback to similarity)
    """
    state = make_initial_state("Test query", "session-002")
    state["similarity_score"] = 0.75
    state["rerank_score"] = None
    state["declined"] = False

    result = await confidence_node(state, mock_config)

    # Without reranker, use similarity directly
    assert result["confidence_score"] == 0.75
    assert result["declined"] is False  # High score, not declined


@pytest.mark.asyncio
async def test_confidence_node_layer1_fast_path(mock_config):
    """Test Layer 1 fast-path in confidence_node (T060).

    Given: declined=True (from retrieval node, Layer 1)
    Expected: Return immediately without fusion computation
    """
    state = make_initial_state("Test query", "session-003")
    state["similarity_score"] = 0.5
    state["rerank_score"] = 0.9
    state["declined"] = True  # Already declined at Layer 1

    result = await confidence_node(state, mock_config)

    # Should skip fusion and return immediately
    assert result["declined"] is True
    assert result["confidence_score"] == 0.5  # Just the similarity, no fusion


@pytest.mark.asyncio
async def test_confidence_node_layer2_threshold(mock_config):
    """Test Layer 2 threshold in confidence_node.

    Given: similarity=0.60, rerank=None, no previous decline
    Expected: confidence_score=0.60, but declined=True (below 0.70 threshold)
    """
    state = make_initial_state("Test query", "session-004")
    state["similarity_score"] = 0.60
    state["rerank_score"] = None
    state["declined"] = False

    result = await confidence_node(state, mock_config)

    assert result["confidence_score"] == 0.60
    assert result["declined"] is True  # Below AGENT_CONFIDENCE_THRESHOLD


def test_route_after_confidence_info_query_borderline():
    """Test _route_after_confidence for INFO_QUERY borderline case.

    Given: intent=INFO_QUERY, similarity=0.6 (< 0.70 threshold), not pre-declined
    Expected: Route to escalation_node (FR-007 borderline escalation)
    """
    state = make_initial_state("Test query", "session-005")
    state["intent"] = "INFO_QUERY"
    state["similarity_score"] = 0.6
    state["declined"] = False

    route = _route_after_confidence(state)

    assert route == "escalation_node"


def test_route_after_confidence_accepted():
    """Test _route_after_confidence for accepted query.

    Given: intent=PRICING, similarity=0.85 (high)
    Expected: Route to answer_node (proceed with response)
    """
    state = make_initial_state("Test query", "session-006")
    state["intent"] = "PRICING"
    state["similarity_score"] = 0.85
    state["declined"] = False

    route = _route_after_confidence(state)

    assert route == "answer_node"


def test_route_after_confidence_declined():
    """Test _route_after_confidence for declined query.

    Given: declined=True (Layer 1 or Layer 2)
    Expected: Route to answer_node (return decline message)
    """
    state = make_initial_state("Test query", "session-007")
    state["declined"] = True

    route = _route_after_confidence(state)

    assert route == "answer_node"


def test_route_after_confidence_info_query_high_confidence():
    """Test _route_after_confidence for INFO_QUERY with high confidence.

    Given: intent=INFO_QUERY, similarity=0.8 (high)
    Expected: Route to answer_node (not borderline)
    """
    state = make_initial_state("Test query", "session-008")
    state["intent"] = "INFO_QUERY"
    state["similarity_score"] = 0.8
    state["declined"] = False

    route = _route_after_confidence(state)

    assert route == "answer_node"  # Not borderline, proceed normally


# Phase 6 tests (T073-T074)


@pytest.mark.asyncio
async def test_confidence_node_layer2_guard_fires(mock_config):
    """T073: Layer 2 guard fires for similarity=0.55 (below 0.70 threshold)."""
    state = make_initial_state("Test query", "session-009")
    state["similarity_score"] = 0.55
    state["rerank_score"] = None
    state["declined"] = False

    result = await confidence_node(state, mock_config)

    assert result["declined"] is True
    assert abs(result["confidence_score"] - 0.55) < 0.01


@pytest.mark.asyncio
async def test_confidence_node_accepted(mock_config):
    """T074: similarity=0.85 → accepted (above 0.70 threshold)."""
    state = make_initial_state("Test query", "session-010")
    state["similarity_score"] = 0.85
    state["rerank_score"] = None
    state["declined"] = False

    result = await confidence_node(state, mock_config)

    assert result["declined"] is False
    assert abs(result["confidence_score"] - 0.85) < 0.01
