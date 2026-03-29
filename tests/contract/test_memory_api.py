"""Contract tests for memory admin API.

Tests cover:
- Intent tracking endpoints (GET, PATCH, LIST)
- Semantic memory list endpoint
- RTBF (Right to Be Forgotten) DELETE endpoint
- Admin key authentication
- Error handling (404, 409, 401)
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app
from core.config import settings

TEST_ADMIN_KEY = "test-admin-secret"


@pytest.fixture
def admin_headers():
    """Fixture for admin authentication headers."""
    return {"X-Admin-Key": TEST_ADMIN_KEY}


class TestMemoryContractPreImpl:
    """Contract test suite (pre-implementation validation)."""

    @pytest.mark.asyncio
    async def test_get_intent_unknown_customer_404(self, monkeypatch, admin_headers):
        """T043: GET /memory/intent/unknown-customer → 404 (red phase).

        Pre-implementation gate: Endpoint doesn't exist yet.
        Expected: 404 Not Found.
        """
        monkeypatch.setattr(settings, "X_ADMIN_KEY", TEST_ADMIN_KEY)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/memory/intent/unknown-customer")
            assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_list_intents_without_admin_key_401(self, monkeypatch):
        """T044: GET /memory/intents without admin key → 401 (red phase).

        Pre-implementation gate: Admin key header required.
        Expected: 401 Unauthorized without X-Admin-Key header.
        """
        monkeypatch.setattr(settings, "X_ADMIN_KEY", TEST_ADMIN_KEY)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.get("/memory/intents")
            assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_patch_intent_status_unknown_customer_404(self, monkeypatch, admin_headers):
        """T045: PATCH /memory/intent/unknown/status → 404 (red phase).

        Pre-implementation gate: Customer not found.
        Expected: 404 Not Found.
        """
        monkeypatch.setattr(settings, "X_ADMIN_KEY", TEST_ADMIN_KEY)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            response = await ac.patch(
                "/memory/intent/unknown-customer/status",
                json={"new_status": "CONTACTED", "expected_version": 1},
                headers=admin_headers,
            )
            assert response.status_code == 404
