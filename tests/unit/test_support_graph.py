"""Support graph wiring — node set and the two conditional edges."""

from __future__ import annotations

from core.support.graph import (
    SUPPORT_GRAPH_NODES,
    _route_after_support_confidence,
    _route_after_support_router,
    build_support_graph,
)

_FORBIDDEN = {
    "hitl_guard_node",
    "order_execution_node",
    "cancellation_node",
    "customer_support_node",
    "escalation_node",
    "queue_consumer_node",
    "router_node",
    "answer_node",
    "clarify_node",
}


def test_compiled_graph_has_only_support_and_shared_nodes():
    graph = build_support_graph()
    nodes = set(graph.get_graph().nodes) - {"__start__", "__end__"}
    assert nodes == set(SUPPORT_GRAPH_NODES)
    assert not nodes & _FORBIDDEN


def test_router_edge():
    assert _route_after_support_router({"intent": "SMALLTALK"}) == "support_answer_node"
    for intent in ("INFO_QUERY", "PRICING", "COMPLAINT"):
        assert _route_after_support_router({"intent": intent}) == "retrieval_node"


def test_confidence_edge_clarify_only_for_non_complaint():
    assert (
        _route_after_support_confidence({"intent": "INFO_QUERY", "needs_clarification": True})
        == "support_clarify_node"
    )
    assert (
        _route_after_support_confidence({"intent": "COMPLAINT", "needs_clarification": True})
        == "support_answer_node"
    )


def test_confidence_edge_never_escalates_or_pauses():
    risky = {
        "intent": "PRICING",
        "declined": False,
        "needs_clarification": False,
        "confidence_score": 0.2,
        "similarity_score": 0.5,
        "hitl_rejection_reason": "clarify_exhausted_still_ambiguous",
        "risk_signals": ["clarify_loop"],
    }
    assert _route_after_support_confidence(risky) == "support_answer_node"
