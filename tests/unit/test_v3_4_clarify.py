"""Unit tests for WP-V3-4 borderline clarify loop.

Why: WP-V3-4 expands clarify eligibility to borderline INFO_QUERY, AVAILABILITY,
and COMPARISON queries when similarity_gap is small (<= CLARIFY_SIMILARITY_GAP_MAX),
enabling real router queries (which classify vague product questions as INFO_QUERY)
to reach clarify_node rather than bypassing it.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agent.nodes.confidence import _route_after_confidence, confidence_node
from core.agent.state import make_initial_state
from core.config import settings


@pytest.fixture
def mock_config():
    mock_db = AsyncMock()
    mock_db.execute.return_value = MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    return {"configurable": {"thread_id": "test-v3-4", "db": mock_db}}


@pytest.mark.asyncio
async def test_confidence_node_v3_4_info_query_small_gap_clarifies(mock_config):
    """Borderline INFO_QUERY with small similarity_gap (<= 0.05) sets needs_clarification=True."""
    state = make_initial_state(
        "Điện thoại Samsung ấy còn hàng không?", "session-v3-4-1", "cust_001"
    )
    state["intent"] = "INFO_QUERY"
    state["similarity_score"] = 0.60
    state["similarity_gap"] = 0.0019  # small gap (< 0.05)
    state["rerank_score"] = None
    state["declined"] = False

    result = await confidence_node(state, mock_config)

    assert result["confidence_score"] == 0.60
    assert result["declined"] is False
    assert result["needs_clarification"] is True

    # Route after confidence should go to clarify_node
    state.update(result)
    route = _route_after_confidence(state)
    assert route == "clarify_node"


@pytest.mark.asyncio
async def test_confidence_node_v3_4_info_query_large_gap_escalates(mock_config):
    """Borderline INFO_QUERY with large similarity_gap (> 0.05) does NOT clarify;
    goes to escalation.
    """
    state = make_initial_state(
        "Chi tiết điện thoại Samsung Galaxy S24 Ultra", "session-v3-4-2", "cust_001"
    )
    state["intent"] = "INFO_QUERY"
    state["similarity_score"] = 0.60
    state["similarity_gap"] = 0.15  # large gap (> 0.05) - clear top candidate, not vague
    state["rerank_score"] = None
    state["declined"] = False

    result = await confidence_node(state, mock_config)

    assert result["confidence_score"] == 0.60
    assert result["declined"] is False
    assert result["needs_clarification"] is False

    # Route after confidence should go to escalation_node
    state.update(result)
    route = _route_after_confidence(state)
    assert route == "escalation_node"


@pytest.mark.asyncio
async def test_confidence_node_v3_4_pricing_small_gap_escalates(mock_config):
    """Borderline PRICING query with small gap does NOT clarify (PRICING keeps old path)."""
    state = make_initial_state("Giá Samsung bao nhiêu?", "session-v3-4-3", "cust_001")
    state["intent"] = "PRICING"
    state["similarity_score"] = 0.60
    state["similarity_gap"] = 0.01
    state["rerank_score"] = None
    state["declined"] = False

    result = await confidence_node(state, mock_config)

    assert result["declined"] is False
    assert result["needs_clarification"] is False

    state.update(result)
    route = _route_after_confidence(state)
    assert route == "escalation_node"


@pytest.mark.asyncio
async def test_confidence_node_v3_4_kill_switch_disabled(mock_config, monkeypatch):
    """When CLARIFY_ENABLED=False, borderline INFO_QUERY with small gap does NOT clarify."""
    monkeypatch.setattr(settings, "CLARIFY_ENABLED", False)

    state = make_initial_state(
        "Điện thoại Samsung ấy còn hàng không?", "session-v3-4-4", "cust_001"
    )
    state["intent"] = "INFO_QUERY"
    state["similarity_score"] = 0.60
    state["similarity_gap"] = 0.01
    state["declined"] = False

    result = await confidence_node(state, mock_config)

    assert result["declined"] is False
    assert result["needs_clarification"] is False

    state.update(result)
    route = _route_after_confidence(state)
    assert route == "escalation_node"


@pytest.mark.asyncio
async def test_confidence_node_v3_4_anti_loop_clarify_count(mock_config):
    """When clarify_count >= quota (v3-0 P2: CLARIFY_MAX_ROUNDS=2), budget is
    spent so the next pass does NOT clarify."""
    state = make_initial_state("Điện thoại Samsung ấy", "session-v3-4-5", "cust_001")
    state["intent"] = "INFO_QUERY"
    state["similarity_score"] = 0.60
    state["similarity_gap"] = 0.01
    state["clarify_count"] = 2  # quota spent (no citations → no handoff either)

    result = await confidence_node(state, mock_config)

    assert result["declined"] is False
    assert result["needs_clarification"] is False

    state.update(result)
    route = _route_after_confidence(state)
    assert route == "escalation_node"
