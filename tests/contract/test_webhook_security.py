"""Contract tests for Telegram webhook secret validation (T049-T051)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from api import dependencies
from api.main import app


def _payload() -> dict:
    return {
        "update_id": 951000001,
        "message": {
            "message_id": 42,
            "from": {"id": 888111222, "is_bot": False, "first_name": "Sec"},
            "chat": {"id": 888111222, "type": "private"},
            "date": int(datetime.now(UTC).timestamp()),
            "text": "security test",
        },
    }


@pytest.mark.asyncio
async def test_missing_secret_header_returns_401(monkeypatch) -> None:
    monkeypatch.setattr(
        dependencies.settings,
        "TELEGRAM_WEBHOOK_SECRET",
        "required_secret_1234567890",
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/webhooks/telegram", json=_payload())

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook secret"


@pytest.mark.asyncio
async def test_invalid_secret_header_returns_401(monkeypatch) -> None:
    monkeypatch.setattr(
        dependencies.settings,
        "TELEGRAM_WEBHOOK_SECRET",
        "required_secret_1234567890",
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/telegram",
            json=_payload(),
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong_secret"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook secret"


@pytest.mark.asyncio
async def test_valid_secret_allows_processing(monkeypatch) -> None:
    expected_secret = "required_secret_1234567890"
    monkeypatch.setattr(dependencies.settings, "TELEGRAM_WEBHOOK_SECRET", expected_secret)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/telegram",
            json=_payload(),
            headers={"X-Telegram-Bot-Api-Secret-Token": expected_secret},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
