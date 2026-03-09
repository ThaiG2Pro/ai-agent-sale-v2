"""Latency and performance verification for HITL system (T074, T075).

Why this exists: Verifies SC-002 (< 200ms API latency) and strict async compliance.
What it does: Measures p95 response times for critical HITL paths.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.dependencies import get_agent_graph
from api.main import app
from core.agent.nodes.queue_consumer import queue_consumer_node
from core.config import settings
from services.database import get_db
from services.hitl.schemas import QueuedMessageBatch, QueueIntentResult

if TYPE_CHECKING:
    from core.agent.state import AgentState

# Standard admin key for tests
TEST_ADMIN_KEY = "test-admin-secret"


@pytest.fixture
def mock_graph():
    """Mock graph to isolate API latency from LLM/Graph overhead."""
    graph = AsyncMock()
    graph.aget_state.return_value = AsyncMock(values={}, next=[])
    graph.aupdate_state.return_value = None
    graph.ainvoke.return_value = {}
    return graph


@pytest.mark.asyncio
async def test_hitl_review_endpoint_latency(monkeypatch, mock_graph):
    """T074: POST /hitl/review p95 latency < 200ms."""
    monkeypatch.setattr(settings, "X_ADMIN_KEY", TEST_ADMIN_KEY)

    # Setup mock DB result for idempotency and status check
    mock_db = AsyncMock()
    mock_result = MagicMock()

    # Define a side effect that keeps returning values
    def db_side_effect():
        # Each iteration in submit_review calls:
        # 1. check_idempotency (in router) -> res.scalar_one_or_none()
        # 2. Terminal status gate (in router) -> res.scalar_one_or_none()
        # 3. check_idempotency (in process_approve) -> res.scalar_one_or_none()
        while True:
            yield None  # check_idempotency (1)
            yield MagicMock(status="paused", admin_id=None)  # status gate (2)
            yield None  # check_idempotency (3)

    mock_result.scalar_one_or_none.side_effect = db_side_effect()
    mock_result.rowcount = 1
    mock_db.execute.return_value = mock_result

    app.dependency_overrides[get_agent_graph] = lambda: mock_graph
    app.dependency_overrides[get_db] = lambda: mock_db

    latencies = []
    payload = {
        "session_id": "perf-test",
        "pause_id": "019cd190-ba86-7432-a627-a302b57fc141",
        "action": "approve",
        "expected_version": 0,
        "admin_user_id": "admin1",
    }
    headers = {
        "X-Admin-Key": TEST_ADMIN_KEY,
        "X-Idempotency-Key": "perf-key",  # Will change per iteration
    }

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            for i in range(10):
                headers["X-Idempotency-Key"] = f"perf-key-{i}"

                start = time.perf_counter()
                resp = await ac.post("/hitl/review", json=payload, headers=headers)
                end = time.perf_counter()

                assert resp.status_code == 200
                latencies.append((end - start) * 1000)

        # Sort and get p95
        latencies.sort()
        p95 = latencies[int(len(latencies) * 0.95)]

        print(f"\n[PERF] POST /hitl/review p95: {p95:.2f}ms")
        # SC-002: < 200ms target
        assert p95 < 200.0
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_queue_consumer_batch_classification_latency():
    """T075: queue_consumer_node processing < 500ms (mocked LLM)."""
    # 1. Setup mocks
    mock_db = AsyncMock()
    mock_result = MagicMock()
    # Mock 5 queued messages
    msgs = [MagicMock(message_id=f"m{i}", message_text=f"text{i}") for i in range(5)]
    mock_result.scalars().all.return_value = msgs
    mock_db.execute.return_value = mock_result

    mock_config = {"configurable": {"db": mock_db}}
    state: AgentState = {
        "session_id": "perf-queue",
        "messages": [],
        "hitl_escalation_count": 0,
    }

    # 2. Mock LiteLLM to be fast
    batch_result = QueuedMessageBatch(
        session_id="perf-queue",
        messages=[
            QueueIntentResult(
                message_id=f"m{i}", text=f"text{i}", intent="CONFIRM", confidence=0.9
            )
            for i in range(5)
        ],
    )
    mock_llm_resp = MagicMock()
    mock_llm_resp.choices[0].message.content = batch_result

    with patch("litellm.acompletion", AsyncMock(return_value=mock_llm_resp)):
        start = asyncio.get_event_loop().time()
        await queue_consumer_node(state, mock_config)
        end = asyncio.get_event_loop().time()

        elapsed_ms = (end - start) * 1000
        print(f"[PERF] queue_consumer_node latency: {elapsed_ms:.2f}ms")

        # Target < 500ms (internal processing overhead)
        assert elapsed_ms < 500.0
