"""Spacely support graph — a second StateGraph over the shared AgentState.

Why a second graph instead of a persona switch inside the sales graph: the
sales graph is a state machine around orders (hitl_guard, order_execution,
cancellation, customer_support queue). A support desk needs none of those,
and gating them per request would couple every future shop change to
Spacely. Here the shop graph is untouched; only the domain-neutral nodes are
shared (retrieval_node, memory_retrieval_node, confidence_node) plus the
same checkpointer and tracing wrapper.

Shape:
    START → support_router_node ─┬→ support_answer_node (SMALLTALK)
                                 └→ retrieval_node → memory_retrieval_node → confidence_node
                                                                                   ├→ support_clarify_node → support_answer_node
                                                                                   └→ support_answer_node
    support_answer_node → END
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from core.agent.graph import traced_node
from core.agent.nodes.confidence import confidence_node
from core.agent.nodes.memory_retrieval import memory_retrieval_node
from core.agent.nodes.retrieval import retrieval_node
from core.agent.state import AgentState
from core.support.nodes.answer import support_answer_node
from core.support.nodes.clarify import support_clarify_node
from core.support.nodes.router import support_router_node

_NODE_FUNCS: dict[str, Any] = {
    "support_router_node": support_router_node,
    "retrieval_node": retrieval_node,
    "memory_retrieval_node": memory_retrieval_node,
    "confidence_node": confidence_node,
    "support_clarify_node": support_clarify_node,
    "support_answer_node": support_answer_node,
}
SUPPORT_GRAPH_NODES = frozenset(_NODE_FUNCS)


def _route_after_support_router(state: AgentState) -> str:
    """Mirror of support_router_node's Command(goto=…) for diagram rendering."""
    return "support_answer_node" if state.get("intent") == "SMALLTALK" else "retrieval_node"


def _route_after_support_confidence(state: AgentState) -> str:
    """Only two exits: clarify or answer. No escalation/HITL/customer_support.

    - COMPLAINT always answers (apology + handoff), even when confidence_node
      flagged needs_clarification or declined.
    - `clarify_exhausted_still_ambiguous` (shop: human handoff) answers on the
      retrieved context instead — there is no review queue for Spacely.
    """
    if state.get("needs_clarification") and state.get("intent") != "COMPLAINT":
        return "support_clarify_node"
    return "support_answer_node"


def build_support_graph(checkpointer=None):
    builder = StateGraph(AgentState)
    for name, func in _NODE_FUNCS.items():
        builder.add_node(name, traced_node(name, func))

    builder.add_edge(START, "support_router_node")
    builder.add_conditional_edges(
        "support_router_node",
        _route_after_support_router,
        {"support_answer_node": "support_answer_node", "retrieval_node": "retrieval_node"},
    )
    builder.add_edge("retrieval_node", "memory_retrieval_node")
    builder.add_edge("memory_retrieval_node", "confidence_node")
    builder.add_conditional_edges(
        "confidence_node",
        _route_after_support_confidence,
        {
            "support_clarify_node": "support_clarify_node",
            "support_answer_node": "support_answer_node",
        },
    )
    builder.add_edge("support_clarify_node", "support_answer_node")
    builder.add_edge("support_answer_node", END)
    return builder.compile(checkpointer=checkpointer, name="spacely-support")
