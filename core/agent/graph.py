"""Graph builder for LangGraph sales agent (T050-T052).

Why: Assembles all nodes into a StateGraph with proper edges and routing.

What: Creates StateGraph(AgentState), adds 5 nodes (router, retrieval,
confidence, escalation, answer), wires edges with conditional routing,
and exports mermaid diagram for documentation.
"""

from __future__ import annotations

import functools
import inspect
import json
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph
from openinference.semconv.trace import SpanAttributes
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
from core.agent.nodes.router import router_node
from core.agent.nodes.state_freshness import state_freshness_validator_node
from core.agent.state import AgentState, NodeStreamEvent
from core.config import settings

tracer = trace.get_tracer("ai_sales_agent.graph")


def _format_clean_node_input(node_name: str, state: Any) -> str:
    """Format a compact, signal-rich input JSON for Phoenix trace display."""
    if not isinstance(state, dict):
        return ""
    if node_name == "router_node":
        payload = {
            "user_message": state.get("user_message"),
            "previous_intent": state.get("intent"),
            "customer_id": state.get("customer_id"),
        }
    elif node_name == "retrieval_node":
        payload = {
            "query": state.get("user_message"),
            "intent": state.get("intent"),
            "clarify_count": state.get("clarify_count", 0),
        }
    elif node_name == "confidence_node":
        payload = {
            "intent": state.get("intent"),
            "similarity_score": state.get("similarity_score"),
            "citations_count": len(state.get("citations") or []),
        }
    elif node_name == "hitl_guard_node":
        payload = {
            "intent": state.get("intent"),
            "confidence_score": state.get("confidence_score"),
            "order_info": state.get("order_info"),
            "risk_signals": state.get("risk_signals", []),
        }
    elif node_name == "answer_node":
        payload = {
            "intent": state.get("intent"),
            "confidence_score": state.get("confidence_score"),
            "citations_count": len(state.get("citations") or []),
            "declined": state.get("declined", False),
        }
    else:
        payload = {
            "intent": state.get("intent"),
            "user_message": state.get("user_message"),
        }
    return json.dumps(payload, ensure_ascii=False, default=str)


def _format_clean_node_output(node_name: str, res: Any) -> str:
    """Format a compact, signal-rich output JSON for Phoenix trace display."""
    update = _unwrap_output_state(res)
    goto = getattr(res, "goto", None)

    if node_name == "router_node":
        payload = {
            "intent": update.get("intent"),
            "confidence": update.get("intent_confidence"),
            "reasoning": update.get("router_reasoning") or update.get("reasoning"),
            "intent_shift": update.get("intent_shift"),
            "goto": goto,
        }
    elif node_name == "retrieval_node":
        citations = update.get("citations") or []
        formatted_citations = []
        for c in citations[:3]:
            if hasattr(c, "name") and hasattr(c, "sku"):
                formatted_citations.append({"name": c.name, "sku": c.sku})
            elif isinstance(c, dict):
                formatted_citations.append({"name": c.get("name"), "sku": c.get("sku")})
        payload = {
            "similarity_score": update.get("similarity_score"),
            "similarity_gap": update.get("similarity_gap"),
            "citations_sample": formatted_citations,
            "total_citations": len(citations),
        }
    elif node_name == "confidence_node":
        payload = {
            "confidence_score": update.get("confidence_score"),
            "declined": update.get("declined"),
            "needs_clarification": update.get("needs_clarification"),
        }
    elif node_name == "hitl_guard_node":
        payload = {
            "hitl_triggered": update.get("hitl_triggered"),
            "hitl_approved": update.get("hitl_approved"),
            "risk_score": update.get("risk_score"),
            "hitl_pause_id": update.get("hitl_pause_id"),
            "goto": goto,
        }
    elif node_name == "answer_node":
        resp = update.get("response") or ""
        payload = {
            "response": resp[:250] + ("..." if len(resp) > 250 else ""),
            "model_used": update.get("model_used"),
            "declined": update.get("declined"),
        }
    else:
        payload = {k: v for k, v in update.items() if not k.startswith("_")}
        if goto:
            payload["goto"] = goto
    return json.dumps(payload, ensure_ascii=False, default=str)


def _extract_attributes_from_state(
    state: Any, kwargs: dict[str, Any], args: tuple[Any, ...]
) -> dict[str, Any]:
    """Extract span attributes (session.id, user.id, intent, model_used, declined) without PII.

    Uses OpenInference attribute names (session.id / user.id) so node spans match the
    `using_attributes(...)` tagging on the parent trace and Phoenix session filters.
    Enforces R-SEC-002: no raw user message or PII in span attributes.
    """
    attrs: dict[str, Any] = {}
    if isinstance(state, dict):
        if session_id := state.get("session_id"):
            attrs[SpanAttributes.SESSION_ID] = str(session_id)
        if customer_id := state.get("customer_id"):
            attrs[SpanAttributes.USER_ID] = str(customer_id)
        if intent := state.get("intent"):
            attrs["intent"] = str(intent)
        if model_used := state.get("model_used"):
            attrs["model_used"] = str(model_used)
        if router_model := state.get("router_model"):
            attrs["router_model"] = str(router_model)
        if reasoning := (state.get("router_reasoning") or state.get("reasoning")):
            attrs["router_reasoning"] = str(reasoning)
        if (declined := state.get("declined")) is not None:
            attrs["declined"] = bool(declined)
        if risk_signals := state.get("risk_signals"):
            attrs["risk_signals"] = ",".join(str(s) for s in risk_signals)
        if state.get("degraded"):
            attrs["outcome"] = "queued"
        elif state.get("declined"):
            attrs["outcome"] = "declined"
        elif state.get("hitl_triggered") or state.get("hitl_rejection_reason"):
            attrs["outcome"] = "handoff"
        elif state.get("response"):
            attrs["outcome"] = "self_handled"

    if SpanAttributes.SESSION_ID not in attrs:
        config = kwargs.get("config") or (
            args[1] if len(args) > 1 and isinstance(args[1], dict) else None
        )
        if isinstance(config, dict):
            thread_id = config.get("configurable", {}).get("thread_id")
            if thread_id:
                attrs[SpanAttributes.SESSION_ID] = str(thread_id)

    return attrs


def _update_span_attributes(span: trace.Span, attrs: dict[str, Any]) -> None:
    for k, v in attrs.items():
        if v is not None:
            span.set_attribute(k, v)


def _annotate_span_start(span: trace.Span, node_name: str, attrs: dict[str, Any]) -> None:
    span.set_attribute("node.name", node_name)
    span.set_attribute(SpanAttributes.OPENINFERENCE_SPAN_KIND, "CHAIN")
    _update_span_attributes(span, attrs)


def _unwrap_output_state(res: Any) -> dict[str, Any]:
    # Command(update={...}) → its update dict; guard isinstance because a plain
    # dict's own `.update` attribute is the bound method, not a dict.
    if hasattr(res, "update") and isinstance(res.update, dict):
        return res.update
    return res if isinstance(res, dict) else {}


def traced_node(node_name: str, func: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a graph node function with an OpenTelemetry span (WP-V3-2).

    Respects settings.OTEL_NODE_SPANS_ENABLED kill-switch — read at graph-build
    time, so toggling the env var requires an app restart.
    Extracts attributes from state without PII (R-SEC-002).
    Handles both async and sync node functions via inspect.iscoroutinefunction.
    Exceptions propagate unchanged; the span records them with status ERROR
    (start_as_current_span default).
    """

    if not settings.OTEL_NODE_SPANS_ENABLED:
        return func

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def traced_wrapper(*args: Any, **kwargs: Any) -> Any:
            state = args[0] if args else kwargs.get("state")
            with tracer.start_as_current_span(f"node.{node_name}") as span:
                _annotate_span_start(
                    span, node_name, _extract_attributes_from_state(state, kwargs, args)
                )
                span.set_attribute(
                    SpanAttributes.INPUT_VALUE, _format_clean_node_input(node_name, state)
                )
                res = await func(*args, **kwargs)
                _update_span_attributes(
                    span,
                    _extract_attributes_from_state(_unwrap_output_state(res), kwargs, args),
                )
                span.set_attribute(
                    SpanAttributes.OUTPUT_VALUE, _format_clean_node_output(node_name, res)
                )
                return res
    else:

        @functools.wraps(func)
        def traced_wrapper(*args: Any, **kwargs: Any) -> Any:
            state = args[0] if args else kwargs.get("state")
            with tracer.start_as_current_span(f"node.{node_name}") as span:
                _annotate_span_start(
                    span, node_name, _extract_attributes_from_state(state, kwargs, args)
                )
                span.set_attribute(
                    SpanAttributes.INPUT_VALUE, _format_clean_node_input(node_name, state)
                )
                res = func(*args, **kwargs)
                _update_span_attributes(
                    span,
                    _extract_attributes_from_state(_unwrap_output_state(res), kwargs, args),
                )
                span.set_attribute(
                    SpanAttributes.OUTPUT_VALUE, _format_clean_node_output(node_name, res)
                )
                return res

    return traced_wrapper


# Single node registry (WP-V3-2): build_graph() wraps + registers every entry,
# so a new node can't be added without tracing, and GRAPH_NODES can't drift.
_NODE_FUNCS: dict[str, Any] = {
    "router_node": router_node,
    "retrieval_node": retrieval_node,
    "memory_retrieval_node": memory_retrieval_node,
    "confidence_node": confidence_node,
    "clarify_node": clarify_node,
    "escalation_node": escalation_node,
    "answer_node": answer_node,
    "hitl_guard_node": hitl_guard_node,
    "queue_consumer_node": queue_consumer_node,
    "state_freshness_validator_node": state_freshness_validator_node,
    "order_execution_node": order_execution_node,
    "cancellation_node": cancellation_node,
    "customer_support_node": customer_support_node,
}

# All registered node names — used to filter streaming events (T081)
GRAPH_NODES = set(_NODE_FUNCS)

# Week 5: Graph schema version for checkpoint compatibility (FR-018)
# 006: WP-V2-3 adds clarify_node + clarify state channels
# 007: v3-0 P1 (T03) adds intent_shift + intent_disagreement_count channels;
#      intent/secondary_intents become cross-turn channels (no longer wiped)
# 008: v3-0 P2 (T05/T06/T07/T13) adds risk_signals + negotiation_note +
#      complaint_turns + hitl_admin_reason channels; confidence_node gains a
#      clarify-exhausted → customer_support_node handoff edge
# 009: v3-0 P3/P4 (T09/T11/T08) adds turn_started_at + degraded +
#      smalltalk_fastpath channels; router gains zero-LLM fast paths
GRAPH_SCHEMA_VERSION = "009"

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

    # Add all nodes wrapped with traced_node (WP-V3-2) — single registration point
    for node_name, node_func in _NODE_FUNCS.items():
        builder.add_node(node_name, traced_node(node_name, node_func))

    # START → router_node (router returns Command with goto dynamically)
    builder.add_edge(START, "router_node")

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
            # v3-0 P2 (T06/T07): in-catalog ambiguity that exhausted the
            # clarify quota hands off to a human instead of declining.
            "customer_support_node": "customer_support_node",
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
