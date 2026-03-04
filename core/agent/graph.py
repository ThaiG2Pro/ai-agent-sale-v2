"""Graph builder for LangGraph sales agent (T050-T052).

Why: Assembles all nodes into a StateGraph with proper edges and routing.

What: Creates StateGraph(AgentState), adds 5 nodes (router, retrieval,
confidence, escalation, answer), wires edges with conditional routing,
and exports mermaid diagram for documentation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from langgraph.graph import END, START, StateGraph

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from core.agent.nodes.answer import answer_node
from core.agent.nodes.confidence import _route_after_confidence, confidence_node
from core.agent.nodes.escalation import escalation_node
from core.agent.nodes.retrieval import retrieval_node
from core.agent.nodes.router import router_node
from core.agent.state import AgentState, NodeStreamEvent

# All registered node names — used to filter streaming events (T081)
GRAPH_NODES = {
    "router_node",
    "retrieval_node",
    "confidence_node",
    "escalation_node",
    "answer_node",
}


def build_graph(checkpointer=None):
    """Build LangGraph StateGraph for sales agent (T050).

    Nodes:
    1. router_node: Classify intent, route to next node
    2. retrieval_node: Call RAG tool, get citations
    3. confidence_node: Fuse scores, apply guards
    4. escalation_node: Decide premium model escalation (Phase 5)
    5. answer_node: Generate response or decline

    Edges:
    - START → router_node (Command routing)
    - router_node → retrieval/escalation (depends on intent)
    - retrieval_node → confidence_node
    - confidence_node → escalation/answer (conditional)
    - escalation_node → answer_node
    - answer_node → END

    Args:
        checkpointer: Optional LangGraph checkpointer for state persistence

    Returns:
        Compiled StateGraph
    """
    builder = StateGraph(AgentState)

    # Add all nodes
    builder.add_node("router_node", router_node)
    builder.add_node("retrieval_node", retrieval_node)
    builder.add_node("confidence_node", confidence_node)
    builder.add_node("escalation_node", escalation_node)
    builder.add_node("answer_node", answer_node)

    # START → router_node (router returns Command with goto)
    builder.add_edge(START, "router_node")

    # retrieval_node → confidence_node
    builder.add_edge("retrieval_node", "confidence_node")

    # confidence_node → escalation_node OR answer_node (conditional, T051)
    builder.add_conditional_edges("confidence_node", _route_after_confidence)

    # escalation_node → answer_node
    builder.add_edge("escalation_node", "answer_node")

    # answer_node → END (universal trace point)
    builder.add_edge("answer_node", END)

    # Compile with recursion limit
    return builder.compile(checkpointer=checkpointer)


def get_mermaid_diagram() -> str:
    """Export mermaid diagram of agent graph (T052).

    Returns:
        Mermaid diagram string for documentation
    """
    graph = build_graph()
    return graph.get_graph().draw_mermaid()


def export_mermaid_to_file(path: str) -> None:
    """Export mermaid diagram to file (T052).

    Args:
        path: File path to write diagram (e.g., 'docs/week3/agent-graph.mmd')
    """
    diagram = get_mermaid_diagram()
    with open(path, "w") as f:
        f.write(diagram)
    print(f"[INFO] Agent graph exported to {path}")


async def astream_agent(
    message: str,
    session_id: str,
    db=None,
    checkpointer=None,
) -> AsyncGenerator[NodeStreamEvent]:
    """Stream per-node events as graph executes (T081, FR-006).

    Yields a NodeStreamEvent for each node completion containing only the
    delta (fields changed by that node), not the full accumulated state.

    Args:
        message: User message to process
        session_id: Session identifier for thread config
        db: Optional AsyncSession (passed to all nodes needing DB access)
        checkpointer: Optional LangGraph checkpointer

    Yields:
        NodeStreamEvent for each completed graph node
    """
    from core.agent.state import make_initial_state

    graph = build_graph(checkpointer=checkpointer)
    initial_state = make_initial_state(message, session_id)
    config = {"configurable": {"thread_id": session_id}}

    # Pass db to nodes that accept it (retrieval_node, answer_node, escalation_node)
    run_kwargs = {}
    if db is not None:
        run_kwargs["db"] = db

    async for event in graph.astream_events(initial_state, config, version="v2", **run_kwargs):
        if event["event"] == "on_chain_end" and event.get("name") in GRAPH_NODES:
            # Extract delta from node output (not full accumulated state)
            delta = event["data"].get("output") or {}
            # Unwrap Command.update if node returned a Command
            if hasattr(delta, "update"):
                delta = delta.update or {}
            if not isinstance(delta, dict):
                delta = {}
            yield NodeStreamEvent(node_name=event["name"], state_snapshot=delta)
