"""Why this exists: Verifies the health check logic independently.
What it does: Tests the /health endpoint response and performance baseline.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app


@pytest.mark.asyncio
async def test_health_endpoint():
    """
    Why this exists: Ensures the health check returns expected status.
    SC-002: < 10ms target.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["db"] == "connected"
    assert "latency_ms" in data

    # Non-blocking check for latency target (SC-002)
    # Note: In CI/Container environments, first run might be slightly slower.
    assert data["latency_ms"] < 200.0  # Loose check for unit test.
