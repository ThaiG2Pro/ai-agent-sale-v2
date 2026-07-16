"""Unit tests for real ModelTrace metrics (WP3 — Feature 003).

Why this exists: ModelTrace rows previously hardcoded tokens/cost=0, latency=None.
These tests pin the new behavior: extract_llm_metrics pulls real usage numbers
from LiteLLM responses, answer_node writes them into model_traces, and the
premium→economy degrade path flags escalation_failure for real.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agent.nodes.answer import _write_model_trace, answer_node
from core.agent.state import make_initial_state
from services.ai import LLMUsageMetrics, extract_llm_metrics

# ---------------------------------------------------------------------------
# extract_llm_metrics
# ---------------------------------------------------------------------------


def _response_with_usage(prompt=100, completion=50, total=150):
    return SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=prompt, completion_tokens=completion, total_tokens=total
        ),
    )


def test_extract_llm_metrics_real_usage():
    """Real usage block → real token numbers + latency passthrough."""
    metrics = extract_llm_metrics(_response_with_usage(), latency_ms=123.4)

    assert metrics.prompt_tokens == 100
    assert metrics.completion_tokens == 50
    assert metrics.total_tokens == 150
    assert metrics.latency_ms == 123.4
    # Ollama/local models have no pricing → completion_cost fails/0 → cost 0.0
    assert metrics.cost >= 0.0


def test_extract_llm_metrics_total_derived_when_missing():
    """total_tokens missing/0 → derived from prompt + completion."""
    metrics = extract_llm_metrics(_response_with_usage(prompt=10, completion=5, total=0))

    assert metrics.total_tokens == 15


def test_extract_llm_metrics_never_raises_on_garbage():
    """Mock/garbage response (no usable usage) → zeroed metrics, no exception."""
    for garbage in (MagicMock(), SimpleNamespace(), None, object()):
        metrics = extract_llm_metrics(garbage)
        assert metrics.prompt_tokens == 0
        assert metrics.completion_tokens == 0
        assert metrics.cost == 0.0
        assert metrics.latency_ms is None


# ---------------------------------------------------------------------------
# _write_model_trace writes real numbers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_model_trace_with_metrics():
    """metrics provided → insert stmt carries real token/cost/latency values."""
    state = make_initial_state("giá?", "t-trace", "cust_001")
    db = AsyncMock()
    metrics = LLMUsageMetrics(
        prompt_tokens=100, completion_tokens=50, total_tokens=150, cost=0.002, latency_ms=87.5
    )

    await _write_model_trace(
        state, db=db, metadata_={"intended_model": "economy-chat"}, metrics=metrics
    )

    db.execute.assert_awaited_once()
    stmt = db.execute.await_args.args[0]
    params = stmt.compile().params
    assert params["prompt_tokens"] == 100
    assert params["completion_tokens"] == 50
    assert params["total_tokens"] == 150
    assert params["latency_ms"] == 87.5
    assert params["cost"] == 0.002


@pytest.mark.asyncio
async def test_write_model_trace_without_metrics_writes_zeros():
    """No metrics (declined/cache path — no LLM call) → zeros, latency None."""
    state = make_initial_state("giá?", "t-trace-0", "cust_001")
    db = AsyncMock()

    await _write_model_trace(state, db=db, metadata_={"intended_model": None})

    stmt = db.execute.await_args.args[0]
    params = stmt.compile().params
    assert params["prompt_tokens"] == 0
    assert params["total_tokens"] == 0
    assert params["latency_ms"] is None


# ---------------------------------------------------------------------------
# answer_node: real metrics reach the trace + premium degrade path
# ---------------------------------------------------------------------------


def _llm_response(text="ok", prompt=100, completion=50):
    choice = MagicMock()
    choice.message.content = text
    response = MagicMock()
    response.choices = [choice]
    response.usage = SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion, total_tokens=prompt + completion
    )
    return response


@pytest.mark.asyncio
async def test_answer_node_passes_real_metrics_to_trace():
    """Accepted path → _write_model_trace receives metrics with real tokens + latency."""
    state = make_initial_state("Giá laptop?", "t-metrics", "cust_001")
    state["intent"] = "PRICING"
    state["model_used"] = "economy-chat"

    with patch(
        "services.ai.AIGateway.complete",
        new_callable=AsyncMock,
        return_value=_llm_response(),
    ):
        with patch(
            "core.agent.nodes.answer._write_model_trace", new_callable=AsyncMock
        ) as mock_trace:
            result = await answer_node(state, {"configurable": {}})

    assert result["response"] == "ok"
    metrics = mock_trace.call_args.kwargs["metrics"]
    assert metrics is not None
    assert metrics.prompt_tokens == 100
    assert metrics.completion_tokens == 50
    assert metrics.total_tokens == 150
    assert metrics.latency_ms is not None and metrics.latency_ms > 0


@pytest.mark.asyncio
async def test_answer_node_premium_failure_degrades_to_economy():
    """Premium model fails at point of use → economy-chat + escalation_failure=True."""
    state = make_initial_state("Tôi muốn khiếu nại", "t-degrade", "cust_001")
    state["intent"] = "COMPLAINT"
    state["model_used"] = "premium-chat"
    state["escalation_flag"] = True

    with patch(
        "services.ai.AIGateway.complete",
        new_callable=AsyncMock,
        side_effect=[RuntimeError("premium model unavailable"), _llm_response("degraded ok")],
    ) as mock_llm:
        with patch(
            "core.agent.nodes.answer._write_model_trace", new_callable=AsyncMock
        ) as mock_trace:
            result = await answer_node(state, {"configurable": {}})

    assert result["response"] == "degraded ok"
    assert result["model_used"] == "economy-chat"
    assert result["escalation_failure"] is True
    # 1st call premium, 2nd call economy fallback
    assert mock_llm.call_count == 2
    assert mock_llm.call_args_list[0].kwargs["model"] == "premium-chat"
    assert mock_llm.call_args_list[1].kwargs["model"] == "economy-chat"
    # trace metadata reflects the real failure
    metadata_ = mock_trace.call_args.kwargs["metadata_"]
    assert metadata_["escalation_failure"] is True
    assert metadata_["intended_model"] == "economy-chat"


@pytest.mark.asyncio
async def test_answer_node_economy_failure_keeps_error_path():
    """economy-chat itself fails → error response, model None, no infinite retry."""
    state = make_initial_state("Giá?", "t-econ-fail", "cust_001")
    state["intent"] = "PRICING"
    state["model_used"] = "economy-chat"

    with patch(
        "services.ai.AIGateway.complete",
        new_callable=AsyncMock,
        side_effect=RuntimeError("all models down"),
    ) as mock_llm:
        with patch("core.agent.nodes.answer._write_model_trace", new_callable=AsyncMock):
            result = await answer_node(state, {"configurable": {}})

    assert "Lỗi khi tạo phản hồi" in result["response"]
    assert result["model_used"] is None
    assert mock_llm.call_count == 1
