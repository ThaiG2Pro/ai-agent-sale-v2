"""Integration tests for Telegram webhook end-to-end flow (T046)."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from api import dependencies
from api.main import app
from api.webhooks import telegram as telegram_webhook
from models.telegram_updates import TelegramUpdate


def _unique_update_id() -> int:
    return int(time.time_ns() % 1_000_000_000)


@pytest.mark.asyncio
async def test_webhook_acknowledges_and_processes_message_in_background(
    db_session,
    monkeypatch,
) -> None:
    """POST webhook should ack 200 and trigger background processing once."""
    update_id = _unique_update_id()
    chat_id = 456123789
    processed: list[tuple[int, int]] = []
    webhook_secret = "test_webhook_secret_1234567890"

    async def fake_process(update, incoming_chat_id: int) -> None:
        processed.append((update.update_id, incoming_chat_id))

    monkeypatch.setattr(telegram_webhook, "process_telegram_message", fake_process)
    monkeypatch.setattr(
        dependencies.settings,
        "TELEGRAM_WEBHOOK_SECRET",
        webhook_secret,
    )

    payload = {
        "update_id": update_id,
        "message": {
            "message_id": 111,
            "from": {"id": chat_id, "is_bot": False, "first_name": "User"},
            "chat": {"id": chat_id, "type": "private"},
            "date": int(datetime.now(UTC).timestamp()),
            "text": "What products do you have?",
        },
    }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/webhooks/telegram",
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": webhook_secret},
        )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert processed == [(update_id, chat_id)]

    row = (
        await db_session.execute(
            select(TelegramUpdate).where(TelegramUpdate.update_id == update_id)
        )
    ).scalar_one_or_none()
    assert row is not None
    assert row.chat_id == chat_id
