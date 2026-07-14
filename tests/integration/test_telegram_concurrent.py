"""Integration tests for concurrent Telegram webhook handling (T047)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from api import dependencies
from api.main import app
from api.webhooks import telegram as telegram_webhook


@pytest.mark.asyncio
async def test_concurrent_webhooks_ack_without_blocking(monkeypatch) -> None:
    """Concurrent requests should all return 200 and schedule processing."""
    processed: list[int] = []
    webhook_secret = "test_webhook_secret_1234567890"

    async def fake_process(update, _: int, **__) -> None:
        await asyncio.sleep(0)
        processed.append(update.update_id)

    async def fake_not_duplicate(_, __: int) -> bool:
        return False

    async def fake_record(*_, **__) -> None:
        return None

    monkeypatch.setattr(telegram_webhook, "process_telegram_message", fake_process)
    monkeypatch.setattr(telegram_webhook, "check_duplicate_update", fake_not_duplicate)
    monkeypatch.setattr(telegram_webhook, "record_telegram_update", fake_record)
    monkeypatch.setattr(
        dependencies.settings,
        "TELEGRAM_WEBHOOK_SECRET",
        webhook_secret,
    )

    payloads = [
        {
            "update_id": 920000000 + i,
            "message": {
                "message_id": 1000 + i,
                "from": {"id": 333000000 + i, "is_bot": False, "first_name": "U"},
                "chat": {"id": 333000000 + i, "type": "private"},
                "date": int(datetime.now(UTC).timestamp()) + i,
                "text": f"message {i}",
            },
        }
        for i in range(5)
    ]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        responses = await asyncio.gather(
            *[
                client.post(
                    "/webhooks/telegram",
                    json=payload,
                    headers={"X-Telegram-Bot-Api-Secret-Token": webhook_secret},
                )
                for payload in payloads
            ]
        )

    assert all(resp.status_code == 200 for resp in responses)
    assert all(resp.json() == {"ok": True} for resp in responses)
    assert sorted(processed) == sorted(payload["update_id"] for payload in payloads)
