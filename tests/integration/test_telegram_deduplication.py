"""Integration tests for Telegram update deduplication (T048)."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from api import dependencies
from api.main import app
from api.webhooks import telegram as telegram_webhook
from models.telegram_updates import TelegramUpdate


def _unique_update_id() -> int:
    return int(time.time_ns() % 1_000_000_000)


@pytest.mark.asyncio
async def test_duplicate_update_acknowledged_but_not_reprocessed(
    db_session,
    monkeypatch,
) -> None:
    """Second webhook with same update_id should ack 200 and skip processing."""
    update_id = _unique_update_id()
    chat_id = 555333111
    processed: list[int] = []
    webhook_secret = "test_webhook_secret_1234567890"

    async def fake_process(update, _: int) -> None:
        processed.append(update.update_id)

    monkeypatch.setattr(telegram_webhook, "process_telegram_message", fake_process)
    monkeypatch.setattr(
        dependencies.settings,
        "TELEGRAM_WEBHOOK_SECRET",
        webhook_secret,
    )

    payload = {
        "update_id": update_id,
        "message": {
            "message_id": 777,
            "from": {"id": chat_id, "is_bot": False, "first_name": "Dup"},
            "chat": {"id": chat_id, "type": "private"},
            "date": int(datetime.now(UTC).timestamp()),
            "text": "duplicate test",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post(
            "/webhooks/telegram",
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": webhook_secret},
        )
        second = await client.post(
            "/webhooks/telegram",
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": webhook_secret},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == {"ok": True}
    assert second.json() == {"ok": True}
    assert processed == [update_id]

    count = (
        await db_session.execute(
            select(func.count())
            .select_from(TelegramUpdate)
            .where(TelegramUpdate.update_id == update_id)
        )
    ).scalar_one()
    assert count == 1
