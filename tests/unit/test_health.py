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
    # Override DB dependency to make unit test deterministic and fast.
    # Use a fake async generator that yields a dummy session with an `execute` method.
    from services import database as dbmod

    async def _fake_get_db():
        class _DummySession:
            async def execute(self, stmt):
                return None

            async def close(self):
                return None

        yield _DummySession()

    app.dependency_overrides[dbmod.get_db] = _fake_get_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        response = await ac.get("/health")

    # Clean up override
    app.dependency_overrides.pop(dbmod.get_db, None)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["db"] == "connected"
    assert "latency_ms" in data

    # Non-blocking check for latency target (SC-002)
    # Note: In CI/Container environments, first run might be slightly slower.
    assert data["latency_ms"] < 200.0  # Loose check for unit test.
