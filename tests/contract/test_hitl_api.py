"""Contract tests for HITL endpoints (T068, T069).

Verifies API behavior, security headers, idempotency, and error handling
using FastAPI TestClient and an isolated test database.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.dependencies import get_agent_graph
from api.main import app
from core.config import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

# Standard admin key for tests
TEST_ADMIN_KEY = "test-admin-secret"


@pytest.fixture
def mock_graph():
    """Mock CompiledStateGraph to avoid real LLM calls in contract tests."""
    graph = AsyncMock()
    # Mock default responses for graph methods
    state_mock = AsyncMock()
    state_mock.values = {}
    state_mock.next = []
    graph.aget_state.return_value = state_mock
    graph.aupdate_state.return_value = None
    graph.ainvoke.return_value = {}
    return graph


@pytest.mark.asyncio
async def test_hitl_auth_required(monkeypatch, mock_graph):
    """GET /hitl/session/{id}/state requires valid X-Admin-Key (T068)."""
    monkeypatch.setattr(settings, "X_ADMIN_KEY", TEST_ADMIN_KEY)
    app.dependency_overrides[get_agent_graph] = lambda: mock_graph

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. No key
            resp = await ac.get("/hitl/session/s1/state")
            assert resp.status_code == 401

            # 2. Invalid key
            resp = await ac.get("/hitl/session/s1/state", headers={"X-Admin-Key": "wrong"})
            assert resp.status_code == 401

            # 3. Valid key (fails with 404 because session doesn't exist in DB)
            resp = await ac.get("/hitl/session/s1/state", headers={"X-Admin-Key": TEST_ADMIN_KEY})
            assert resp.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_hitl_get_state_not_found(monkeypatch, mock_graph):
    """GET /hitl/session/{id}/state returns 404 if no pause exists (T068)."""
    monkeypatch.setattr(settings, "X_ADMIN_KEY", TEST_ADMIN_KEY)
    app.dependency_overrides[get_agent_graph] = lambda: mock_graph

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(
                "/hitl/session/non-existent/state", headers={"X-Admin-Key": TEST_ADMIN_KEY}
            )
            assert resp.status_code == 404
            assert "not found" in resp.json()["detail"].lower()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_hitl_review_idempotency(monkeypatch, db_session: AsyncSession, mock_graph):
    """POST /hitl/review enforces idempotency (T069)."""
    monkeypatch.setattr(settings, "X_ADMIN_KEY", TEST_ADMIN_KEY)
    app.dependency_overrides[get_agent_graph] = lambda: mock_graph

    from uuid_utils import uuid7

    from models.schema import HITLMetadata, InterruptedSession

    # 1. Setup a paused session in DB
    session_id = "sess-idempotency"
    pause_id = uuid7()

    # Add HITLMetadata
    db_session.add(
        HITLMetadata(
            pause_id=pause_id, session_id=session_id, pause_reason="test", status="paused"
        )
    )
    # Add InterruptedSession for version tracking
    db_session.add(
        InterruptedSession(session_id=session_id, next_node="node1", reason="test", version=0)
    )
    await db_session.commit()

    payload = {
        "session_id": session_id,
        "pause_id": str(pause_id),
        "action": "approve",
        "expected_version": 0,
        "admin_user_id": "admin1",
    }

    headers = {"X-Admin-Key": TEST_ADMIN_KEY, "X-Idempotency-Key": "key-123"}

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # First request
            resp1 = await ac.post("/hitl/review", json=payload, headers=headers)
            assert resp1.status_code == 200
            assert resp1.headers["X-Idempotency-Status"] == "new"
            action_id = resp1.json()["action_id"]

            # Second request (replay)
            resp2 = await ac.post("/hitl/review", json=payload, headers=headers)
            assert resp2.status_code == 200
            assert resp2.headers["X-Idempotency-Status"] == "hit"
            assert resp2.json()["action_id"] == action_id

            # Verify graph was only called ONCE
            assert mock_graph.ainvoke.call_count == 1
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_hitl_version_conflict(monkeypatch, db_session: AsyncSession, mock_graph):
    """POST /hitl/review returns 409 on version mismatch (T069)."""
    monkeypatch.setattr(settings, "X_ADMIN_KEY", TEST_ADMIN_KEY)
    app.dependency_overrides[get_agent_graph] = lambda: mock_graph

    from uuid_utils import uuid7

    from models.schema import HITLMetadata, InterruptedSession

    session_id = "sess-conflict"
    pause_id = uuid7()

    db_session.add(
        HITLMetadata(
            pause_id=pause_id, session_id=session_id, pause_reason="test", status="paused"
        )
    )
    db_session.add(
        InterruptedSession(
            session_id=session_id,
            next_node="node1",
            reason="test",
            version=5,  # Current version is 5
        )
    )
    await db_session.commit()

    payload = {
        "session_id": session_id,
        "pause_id": str(pause_id),
        "action": "approve",
        "expected_version": 0,  # Admin thinks it's 0 -> Conflict!
        "admin_user_id": "admin1",
    }

    headers = {"X-Admin-Key": TEST_ADMIN_KEY, "X-Idempotency-Key": "key-conflict"}

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/hitl/review", json=payload, headers=headers)
            assert resp.status_code == 409
            assert "version conflict" in resp.json()["detail"]["error"].lower()
    finally:
        app.dependency_overrides.clear()
