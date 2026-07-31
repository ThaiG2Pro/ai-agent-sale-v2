"""Contract test: Webhook returns 200 OK within 200ms (T022).

Purpose: Verify webhook endpoint acknowledges requests quickly (<200ms)
before starting background processing. Per FR-003 requirement.

This test MUST FAIL initially (TDD) - will pass once endpoint is implemented.
"""

import time
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from api.main import app


@pytest.mark.asyncio
async def test_webhook_response_time_under_200ms():
    """Verify webhook returns 200 OK within 200ms."""
    payload = {
        "update_id": 999888777,
        "message": {
            "message_id": 100,
            "from": {"id": 111111111, "is_bot": False, "first_name": "Speed"},
            "chat": {"id": 111111111, "type": "private"},
            "date": int(datetime.now(UTC).timestamp()),
            "text": "Test response time",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Warmup request to trigger route compilation and lazy initialization
        await client.post(
            "/webhooks/telegram",
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": "dev_test_secret_min_20_chars_12345678"},
        )

        start_time = time.perf_counter()

        response = await client.post(
            "/webhooks/telegram",
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": "dev_test_secret_min_20_chars_12345678"},
        )

        end_time = time.perf_counter()
        elapsed_ms = (end_time - start_time) * 1000

    assert response.status_code == 200
    assert elapsed_ms < 200, f"Response took {elapsed_ms:.2f}ms (limit: 200ms)"


@pytest.mark.asyncio
async def test_webhook_acknowledgment_format():
    """Verify webhook returns proper acknowledgment JSON."""
    payload = {
        "update_id": 888777666,
        "message": {
            "message_id": 101,
            "from": {"id": 222222222, "is_bot": False, "first_name": "Format"},
            "chat": {"id": 222222222, "type": "private"},
            "date": int(datetime.now(UTC).timestamp()),
            "text": "Check format",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/telegram",
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": "dev_test_secret_min_20_chars_12345678"},
        )

    assert response.status_code == 200
    data = response.json()
    assert "ok" in data
    assert data["ok"] is True
