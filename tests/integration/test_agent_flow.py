"""Integration tests for agent flow (T061)."""

from langgraph.checkpoint.memory import MemorySaver

from core.agent.graph import build_graph


def test_graph_structure():
    """Test that build_graph() returns a valid compiled graph."""
    graph = build_graph()

    # Verify graph object
    assert graph is not None
    assert hasattr(graph, "ainvoke")
    assert hasattr(graph, "invoke")


def test_mermaid_diagram_generation():
    """Test that mermaid diagram can be generated."""
    from core.agent.graph import get_mermaid_diagram

    diagram = get_mermaid_diagram()

    # Verify diagram contains key nodes
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


# Full integration tests with actual graph invocation deferred to Phase 5
# Phase 5 will include DI setup for DB session injection into retrieval_node
