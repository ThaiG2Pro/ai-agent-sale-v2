"""Contract tests for health probe endpoints (T099-T101)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app


@pytest.mark.asyncio
async def test_liveness_contract_returns_alive_status() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/liveness")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "alive"
    assert isinstance(payload["timestamp"], (int, float))


@pytest.mark.asyncio
async def test_readiness_contract_returns_db_and_event_loop_checks() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/readiness")

    assert response.status_code in (200, 503)
    payload = response.json()

    assert payload["status"] in {"ready", "not_ready", "degraded"}
    assert isinstance(payload["timestamp"], (int, float))
    assert "checks" in payload
    assert "database" in payload["checks"]
    assert "event_loop" in payload["checks"]
    assert "connection_pool" in payload["checks"]
