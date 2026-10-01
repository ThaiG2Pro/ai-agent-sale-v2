"""POST /support/query — disabled/auth/happy path with the graph mocked."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api import dependencies
from api.main import app
from services import database as dbmod

_BODY = {"message": "credit là gì?", "session_id": "web:s1", "customer_id": "spacely-user-1"}


async def _fake_get_db():
    class _Session:
        async def close(self):
            return None

    yield _Session()


@pytest.fixture
def client(monkeypatch):
    app.dependency_overrides[dbmod.get_db] = _fake_get_db
    monkeypatch.setattr(dependencies.settings, "AGENT_API_KEY", None)
    transport = ASGITransport(app=app)
    yield AsyncClient(transport=transport, base_url="http://test")
    app.dependency_overrides.pop(dbmod.get_db, None)
    app.state.support_graph = None


@pytest.mark.asyncio
async def test_503_when_graph_disabled(client):
    app.state.support_graph = None
    async with client as ac:
        r = await ac.post("/support/query", json=_BODY)
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_401_when_key_set_and_missing(client, monkeypatch):
    monkeypatch.setattr(dependencies.settings, "AGENT_API_KEY", "k1")
    app.state.support_graph = object()
    async with client as ac:
        missing = await ac.post("/support/query", json=_BODY)
        wrong = await ac.post("/support/query", json=_BODY, headers={"X-Agent-Key": "nope"})
    assert missing.status_code == 401 and wrong.status_code == 401


@pytest.mark.asyncio
async def test_200_happy_path_maps_state(client, monkeypatch):
    monkeypatch.setattr(dependencies.settings, "AGENT_API_KEY", "k1")
    final_state = {
        "response": "Credit là đơn vị…",
        "declined": False,
        "intent": "PRICING",
        "intent_confidence": 0.91,
        "model_used": "economy-chat",
        "citations": [{"name": "Credit là gì, mua sao?", "sku": "spacely-credit-la-gi"}],
    }
    graph = AsyncMock()
    graph.ainvoke = AsyncMock(return_value=final_state)
    app.state.support_graph = graph
    with patch("api.routes.support.post_turn_tasks", new=AsyncMock()) as post_turn:
        async with client as ac:
            r = await ac.post("/support/query", json=_BODY, headers={"X-Agent-Key": "k1"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["answer"] == "Credit là đơn vị…"
    assert data["intent"] == "PRICING" and data["declined"] is False
    assert data["citations"] == [{"name": "Credit là gì, mua sao?"}]
    assert data["model_used"] == "economy-chat" and data["elapsed_ms"] >= 0
    (init_state,) = graph.ainvoke.await_args.args
    assert init_state["user_message"] == "credit là gì?"
    assert init_state["customer_id"] == "spacely-user-1"
    assert graph.ainvoke.await_args.kwargs["config"]["configurable"]["thread_id"] == "web:s1"
    post_turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_422_on_empty_message(client):
    app.state.support_graph = object()
    async with client as ac:
        r = await ac.post("/support/query", json={**_BODY, "message": ""})
    assert r.status_code == 422
