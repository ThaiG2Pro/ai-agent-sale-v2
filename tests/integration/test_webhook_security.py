"""Integration tests for Telegram webhook security (T065-T068)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from api import dependencies
from api.main import app


def _payload_with_date(ts: int) -> dict:
    return {
        "update_id": int(ts % 1_000_000_000),
        "message": {
            "message_id": 202,
            "from": {"id": 444222111, "is_bot": False, "first_name": "Sec"},
            "chat": {"id": 444222111, "type": "private"},
            "date": ts,
            "text": "security integration",
        },
    }


@pytest.mark.asyncio
async def test_webhook_without_secret_header_returns_401(monkeypatch) -> None:
    monkeypatch.setattr(
        dependencies.settings,
        "TELEGRAM_WEBHOOK_SECRET",
        "integration_secret_1234567890",
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/telegram",
            json=_payload_with_date(int(datetime.now(UTC).timestamp())),
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_webhook_with_wrong_secret_returns_401(monkeypatch) -> None:
    monkeypatch.setattr(
        dependencies.settings,
        "TELEGRAM_WEBHOOK_SECRET",
        "integration_secret_1234567890",
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/telegram",
            json=_payload_with_date(int(datetime.now(UTC).timestamp())),
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_webhook_with_old_timestamp_returns_403(monkeypatch) -> None:
    secret = "integration_secret_1234567890"
    monkeypatch.setattr(dependencies.settings, "TELEGRAM_WEBHOOK_SECRET", secret)
    old_ts = int((datetime.now(UTC) - timedelta(minutes=6)).timestamp())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/telegram",
            json=_payload_with_date(old_ts),
            headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_webhook_with_valid_secret_and_fresh_timestamp_succeeds(monkeypatch) -> None:
    secret = "integration_secret_1234567890"
    monkeypatch.setattr(dependencies.settings, "TELEGRAM_WEBHOOK_SECRET", secret)
    now_ts = int(datetime.now(UTC).timestamp())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/telegram",
            json=_payload_with_date(now_ts),
            headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
