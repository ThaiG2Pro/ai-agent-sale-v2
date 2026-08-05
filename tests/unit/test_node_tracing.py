"""
Why: Unit tests for WP-V3-2 OpenTelemetry per-node tracing (R-SEC-002, kill-switch).
What: Tests traced_node wrapper for async/sync functions, span attribute extraction,
      PII protection, kill-switch behavior, and graph build compatibility.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import core.agent.graph as graph_module
from core.agent.graph import build_graph, traced_node
from core.config import settings


@pytest.fixture
def memory_exporter():
    """Setup in-memory span exporter and patch core.agent.graph.tracer."""
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    test_tracer = provider.get_tracer("test_tracer")

    with patch.object(graph_module, "tracer", test_tracer):
        yield exporter

    exporter.clear()


def test_traced_node_kill_switch_disabled():
    """When OTEL_NODE_SPANS_ENABLED=False, traced_node returns original function."""

    def dummy_sync_node(state: dict) -> dict:
        return {"output": "ok"}

    with patch.object(settings, "OTEL_NODE_SPANS_ENABLED", False):
        wrapped = traced_node("dummy_node", dummy_sync_node)
        assert wrapped is dummy_sync_node


@pytest.mark.asyncio
async def test_traced_node_async_span_creation(memory_exporter):
    """Async node function creates an OTel span with state attributes and output updates."""

    async def sample_async_node(
        state: dict[str, Any], config: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return {"intent": "INFO_QUERY", "model_used": "groq/llama-3.3-70b"}

    with patch.object(settings, "OTEL_NODE_SPANS_ENABLED", True):
        wrapped = traced_node("sample_async_node", sample_async_node)
        input_state = {
            "session_id": "sess-12345",
            "message": "SECRET PII USER MESSAGE nguyen@example.com 0912345678",
        }
        res = await wrapped(input_state)
        assert res == {"intent": "INFO_QUERY", "model_used": "groq/llama-3.3-70b"}

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "node.sample_async_node"

    attrs = span.attributes
    assert attrs.get("node.name") == "sample_async_node"
    assert attrs.get("session_id") == "sess-12345"
    assert attrs.get("intent") == "INFO_QUERY"
    assert attrs.get("model_used") == "groq/llama-3.3-70b"

    # R-SEC-002 Safety Check: Ensure no message / PII content is logged as span attributes
    for _key, val in attrs.items():
        assert "SECRET" not in str(val)
        assert "nguyen@example.com" not in str(val)
        assert "0912345678" not in str(val)


def test_traced_node_sync_span_creation(memory_exporter):
    """Sync node function creates an OTel span with state attributes."""

    def sample_sync_node(state: dict[str, Any]) -> dict[str, Any]:
        return {"declined": True}

    with patch.object(settings, "OTEL_NODE_SPANS_ENABLED", True):
        wrapped = traced_node("sample_sync_node", sample_sync_node)
        input_state = {"session_id": "sess-67890", "intent": "OTHER"}
        res = wrapped(input_state)
        assert res == {"declined": True}

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == "node.sample_sync_node"

    attrs = span.attributes
    assert attrs.get("node.name") == "sample_sync_node"
    assert attrs.get("session_id") == "sess-67890"
    assert attrs.get("intent") == "OTHER"
    assert attrs.get("declined") is True


def test_traced_node_config_session_id_fallback(memory_exporter):
    """If state lacks session_id, fallback to config['configurable']['thread_id']."""

    def sample_node(state: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
        return {}

    with patch.object(settings, "OTEL_NODE_SPANS_ENABLED", True):
        wrapped = traced_node("sample_node", sample_node)
        config = {"configurable": {"thread_id": "thread-abc"}}
        wrapped({}, config=config)

    spans = memory_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].attributes.get("session_id") == "thread-abc"


def test_build_graph_with_traced_nodes():
    """build_graph() compiles successfully with traced_node wrapped nodes."""
    graph = build_graph()
    assert graph is not None
    # Verify graph contains expected nodes
    graph_nodes = graph.get_graph().nodes
    assert "router_node" in graph_nodes
    assert "retrieval_node" in graph_nodes
    assert "answer_node" in graph_nodes
