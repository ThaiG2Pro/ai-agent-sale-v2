"""Unit tests for single-screen test UI endpoints (/ui, /hitl/pending, /agent/session/{session_id}/history)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from api.dependencies import get_agent_graph, verify_admin_key
from api.main import app
from services import database as dbmod


@pytest.mark.asyncio
async def test_get_ui_endpoint_returns_200_html():
    """Ensures GET /ui serves HTML content."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/ui")

    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "AI Sales Agent" in response.text
    assert "Admin Zone" in response.text
    assert "New Session" in response.text


@pytest.mark.asyncio
async def test_get_pending_hitl_endpoint():
    """Ensures GET /hitl/pending returns list of pending sessions."""

    async def _fake_get_db():
        class _DummyResult:
            def scalars(self):
                return self

            def all(self):
                return []

        class _DummySession:
            async def execute(self, stmt):
                return _DummyResult()

            async def close(self):
                pass

        yield _DummySession()

    async def _fake_admin_key():
        return "dev-admin-key"

    app.dependency_overrides[dbmod.get_db] = _fake_get_db
    app.dependency_overrides[verify_admin_key] = _fake_admin_key
    app.dependency_overrides[get_agent_graph] = lambda: MagicMock()

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/hitl/pending", headers={"X-Admin-Key": "dev-admin-key"})

        assert response.status_code == 200
        assert isinstance(response.json(), list)
    finally:
        app.dependency_overrides.pop(dbmod.get_db, None)
        app.dependency_overrides.pop(verify_admin_key, None)
        app.dependency_overrides.pop(get_agent_graph, None)


@pytest.mark.asyncio
async def test_get_session_history_endpoint():
    """Ensures GET /agent/session/{session_id}/history returns session history."""
    fake_graph = MagicMock()
    mock_snapshot = MagicMock()
    mock_snapshot.values = {
        "messages": [
            MagicMock(type="human", content="Xin chào"),
            MagicMock(type="ai", content="Tôi có thể giúp gì cho bạn?"),
        ],
        "order_info": {"product_name": "iPhone 15"},
        "customer_id": "cust-1",
    }
    mock_snapshot.next = []
    fake_graph.aget_state = AsyncMock(return_value=mock_snapshot)

    async def _fake_get_db():
        class _DummySession:
            async def close(self):
                pass

        yield _DummySession()

    app.dependency_overrides[dbmod.get_db] = _fake_get_db
    app.dependency_overrides[get_agent_graph] = lambda: fake_graph

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/agent/session/test-session-123/history")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "test-session-123"
        assert data["exists"] is True
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][0]["content"] == "Xin chào"
        assert data["messages"][1]["role"] == "assistant"
    finally:
        app.dependency_overrides.pop(dbmod.get_db, None)
        app.dependency_overrides.pop(get_agent_graph, None)
