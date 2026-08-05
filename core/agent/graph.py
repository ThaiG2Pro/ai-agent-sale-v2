"""Graph builder for LangGraph sales agent (T050-T052).

Why: Assembles all nodes into a StateGraph with proper edges and routing.

What: Creates StateGraph(AgentState), adds 5 nodes (router, retrieval,
confidence, escalation, answer), wires edges with conditional routing,
and exports mermaid diagram for documentation.
"""

from __future__ import annotations

import functools
import inspect
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph
from opentelemetry import trace

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

from core.agent.nodes.answer import answer_node
from core.agent.nodes.cancellation import cancellation_node
from core.agent.nodes.clarify import clarify_node
from core.agent.nodes.confidence import _route_after_confidence, confidence_node
from core.agent.nodes.customer_support import customer_support_node
from core.agent.nodes.escalation import escalation_node
from core.agent.nodes.hitl_guard import hitl_guard_node
from core.agent.nodes.memory_retrieval import memory_retrieval_node
from core.agent.nodes.order_execution import order_execution_node
from core.agent.nodes.queue_consumer import queue_consumer_node
from core.agent.nodes.retrieval import retrieval_node
from core.agent.nodes.router import _route_after_router, router_node
from core.agent.nodes.state_freshness import state_freshness_validator_node
from core.agent.state import AgentState, NodeStreamEvent
from core.config import settings

tracer = trace.get_tracer("ai_sales_agent.graph")


def _extract_attributes_from_state(
    state: Any, kwargs: dict[str, Any], args: tuple[Any, ...]
) -> dict[str, Any]:
    """Extract span attributes (session_id, intent, model_used, declined) without PII.

    Enforces R-SEC-002: no raw user message or PII in span attributes.
    """
    attrs: dict[str, Any] = {}
    if isinstance(state, dict):
        if session_id := state.get("session_id"):
            attrs["session_id"] = str(session_id)
        if intent := state.get("intent"):
            attrs["intent"] = str(intent)
        if model_used := state.get("model_used"):
            attrs["model_used"] = str(model_used)
        if (declined := state.get("declined")) is not None:
            attrs["declined"] = bool(declined)

    if "session_id" not in attrs:
        config = kwargs.get("config") or (
            args[1] if len(args) > 1 and isinstance(args[1], dict) else None
        )
        if isinstance(config, dict):
            thread_id = config.get("configurable", {}).get("thread_id")
            if thread_id:
                attrs["session_id"] = str(thread_id)

    return attrs


def _update_span_attributes(span: trace.Span, attrs: dict[str, Any]) -> None:
    for k, v in attrs.items():
        if v is not None:
            span.set_attribute(k, v)


def traced_node(node_name: str, func: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a graph node function with an OpenTelemetry span (WP-V3-2).

    Respects settings.OTEL_NODE_SPANS_ENABLED kill-switch.
    Extracts attributes from state without PII (R-SEC-002).
    Handles both async and sync node functions via inspect.iscoroutinefunction.
    """

    if not settings.OTEL_NODE_SPANS_ENABLED:
        return func

    is_async = inspect.iscoroutinefunction(func)

    if is_async:

        @functools.wraps(func)
        async def async_traced_wrapper(*args: Any, **kwargs: Any) -> Any:
            state = args[0] if args else kwargs.get("state")
            state_attrs = _extract_attributes_from_state(state, kwargs, args)

            with tracer.start_as_current_span(f"node.{node_name}") as span:
                span.set_attribute("node.name", node_name)
                _update_span_attributes(span, state_attrs)

                res = await func(*args, **kwargs)

                output_state = (
                    res.update
                    if hasattr(res, "update") and isinstance(res.update, dict)
                    else (res if isinstance(res, dict) else {})
                )
                out_attrs = _extract_attributes_from_state(output_state, kwargs, args)
                _update_span_attributes(span, out_attrs)

                return res

        return async_traced_wrapper
    else:

        @functools.wraps(func)
        def sync_traced_wrapper(*args: Any, **kwargs: Any) -> Any:
            state = args[0] if args else kwargs.get("state")
            state_attrs = _extract_attributes_from_state(state, kwargs, args)

            with tracer.start_as_current_span(f"node.{node_name}") as span:
                span.set_attribute("node.name", node_name)
                _update_span_attributes(span, state_attrs)

                res = func(*args, **kwargs)

                output_state = (
                    res.update
                    if hasattr(res, "update") and isinstance(res.update, dict)
                    else (res if isinstance(res, dict) else {})
                )
                out_attrs = _extract_attributes_from_state(output_state, kwargs, args)
                _update_span_attributes(span, out_attrs)

                return res

        return sync_traced_wrapper


# All registered node names — used to filter streaming events (T081)
GRAPH_NODES = {
    "router_node",
    "retrieval_node",
    "memory_retrieval_node",
    "confidence_node",
    "clarify_node",
    "escalation_node",
    "answer_node",
    "hitl_guard_node",
    "queue_consumer_node",
    "state_freshness_validator_node",
    "order_execution_node",
    "cancellation_node",
    "customer_support_node",
}

# Week 5: Graph schema version for checkpoint compatibility (FR-018)
# 006: WP-V2-3 adds clarify_node + clarify state channels
GRAPH_SCHEMA_VERSION = "006"

# Article X: max 5 turns per conversation. One turn traverses at most ~4 graph
# super-steps (router → retrieval → memory → confidence → escalation/hitl → answer),
# so the per-invoke LangGraph recursion limit is AGENT_MAX_TURNS x 4 = 20 steps
# (LangGraph default 25 would let a mis-wired loop run longer than Article X allows).
AGENT_RECURSION_LIMIT = settings.AGENT_MAX_TURNS * 4


def make_agent_config(session_id: str, db=None) -> dict:
    """Build the standard RunnableConfig for invoking the agent graph.

    Single place that enforces the Article X recursion limit — every
    ainvoke/astream call site must build its config here so a graph loop
    aborts with GraphRecursionError instead of running unbounded.
    """
    return {
        "configurable": {"thread_id": session_id, "db": db},
        "recursion_limit": AGENT_RECURSION_LIMIT,
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

    # Add all nodes wrapped with traced_node (WP-V3-2)
    builder.add_node("router_node", traced_node("router_node", router_node))
    builder.add_node("retrieval_node", traced_node("retrieval_node", retrieval_node))
    builder.add_node(
        "memory_retrieval_node", traced_node("memory_retrieval_node", memory_retrieval_node)
    )
    builder.add_node("confidence_node", traced_node("confidence_node", confidence_node))
    builder.add_node("clarify_node", traced_node("clarify_node", clarify_node))
    builder.add_node("escalation_node", traced_node("escalation_node", escalation_node))
    builder.add_node("answer_node", traced_node("answer_node", answer_node))
    builder.add_node("hitl_guard_node", traced_node("hitl_guard_node", hitl_guard_node))
    builder.add_node(
        "queue_consumer_node", traced_node("queue_consumer_node", queue_consumer_node)
    )

    builder.add_node(
        "state_freshness_validator_node",
        traced_node("state_freshness_validator_node", state_freshness_validator_node),
    )
    builder.add_node(
        "order_execution_node", traced_node("order_execution_node", order_execution_node)
    )
    builder.add_node("cancellation_node", traced_node("cancellation_node", cancellation_node))
    builder.add_node(
        "customer_support_node", traced_node("customer_support_node", customer_support_node)
    )

    # START → router_node (router returns Command with goto)
    builder.add_edge(START, "router_node")

    # router_node returns Command(goto=...) which handles routing
    # For diagram rendering, add conditional edges that mirror the routing logic
    builder.add_conditional_edges(
        "router_node",
        _route_after_router,
        {
            "retrieval_node": "retrieval_node",
            "memory_retrieval_node": "memory_retrieval_node",
            "escalation_node": "escalation_node",
            "answer_node": "answer_node",
            "hitl_guard_node": "hitl_guard_node",
        },
    )

    # retrieval_node → memory_retrieval_node (T136: inject past context before confidence)
    builder.add_edge("retrieval_node", "memory_retrieval_node")

    # memory_retrieval_node → confidence_node (T136: continue to confidence scoring)
    builder.add_edge("memory_retrieval_node", "confidence_node")

    # confidence_node → escalation_node OR hitl_guard_node OR answer_node (conditional, T051)
    builder.add_conditional_edges(
        "confidence_node",
        _route_after_confidence,
        {
            "escalation_node": "escalation_node",
            "hitl_guard_node": "hitl_guard_node",
            "answer_node": "answer_node",
            "clarify_node": "clarify_node",
        },
    )

    # escalation_node → answer_node
    builder.add_edge("escalation_node", "answer_node")

    # WP-V2-3: clarify_node → answer_node (universal trace point traces the
    # clarifying question via Path 0, then END)
    builder.add_edge("clarify_node", "answer_node")

    # HITL graph pathways
    # These nodes return Command(goto=...) directly, so no static edges are needed.
    # Diagram might not show these dynamic edges without explicit config,
    # but runtime execution will be correct.

    builder.add_edge("order_execution_node", "answer_node")
    builder.add_edge("cancellation_node", "answer_node")
    builder.add_edge("customer_support_node", END)

    # answer_node → END (universal trace point)
    builder.add_edge("answer_node", END)

    # Recursion limit is enforced per-invoke via make_agent_config (Article X) —
    # compile() can't set it, and with_config() would hide aget_state/aupdate_state.
    return builder.compile(checkpointer=checkpointer, name="agent-orchestration")


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
    customer_id: str,
    db=None,
    checkpointer=None,
    graph=None,
) -> AsyncGenerator[NodeStreamEvent]:
    """Stream per-node events as graph executes (T081, FR-006).

    Yields a NodeStreamEvent for each node completion containing only the
    delta (fields changed by that node), not the full accumulated state.

    Args:
        message: User message to process
        session_id: Session identifier for thread config
        customer_id: Cross-session customer identifier (required for memory scoping)
        db: Optional AsyncSession — passed to retrieval/answer nodes via configurable
        checkpointer: Optional LangGraph checkpointer
        graph: Optional already built/compiled graph

    Yields:
        NodeStreamEvent for each completed graph node
    """
    from core.agent.state import make_initial_state

    if graph is None:
        graph = build_graph(checkpointer=checkpointer)
    initial_state = make_initial_state(message, session_id=session_id, customer_id=customer_id)
    # Pass db through configurable so nodes can inject via RunnableConfig;
    # recursion limit enforced per Article X (make_agent_config).
    config: dict = make_agent_config(session_id, db=db)

    async for event in graph.astream_events(initial_state, config, version="v2"):
        if event["event"] == "on_chain_end" and event.get("name") in GRAPH_NODES:
            # Extract delta from node output (not full accumulated state)
            delta = event["data"].get("output") or {}
            # Unwrap Command.update if node returned a Command
            if hasattr(delta, "update"):
                delta = delta.update or {}
            if not isinstance(delta, dict):
                delta = {}
            yield NodeStreamEvent(node_name=event["name"], state_snapshot=delta)
