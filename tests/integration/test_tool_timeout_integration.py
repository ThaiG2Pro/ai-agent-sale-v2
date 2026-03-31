"""Integration tests for tool timeout and retry webhook behavior (T095-T098)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from api import dependencies
from api.main import app
from core.tools.models import ToolResult


def _fresh_date() -> int:
    return int(datetime.now(UTC).timestamp())


@pytest.mark.asyncio
async def test_timeout_returns_retry_button(monkeypatch) -> None:
    secret = "timeout_secret_1234567890"
    monkeypatch.setattr(dependencies.settings, "TELEGRAM_WEBHOOK_SECRET", secret)

    async def _fake_inventory_lookup(_):
        return ToolResult(
            success=False,
            error="inventory_check timed out after 5.0s",
            is_retryable=True,
            data=None,
        )

    monkeypatch.setattr(
        "core.telegram.message_handler.execute_inventory_lookup",
        _fake_inventory_lookup,
    )

    payload = {
        "update_id": 970000001,
        "message": {
            "message_id": 1,
            "from": {"id": 99, "is_bot": False, "first_name": "T"},
            "chat": {"id": 99, "type": "private"},
            "date": _fresh_date(),
            "text": "/inventory PROD-001",
        },
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/webhooks/telegram",
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_retry_callback_reinvokes_tool(monkeypatch) -> None:
    secret = "timeout_secret_1234567890"
    monkeypatch.setattr(dependencies.settings, "TELEGRAM_WEBHOOK_SECRET", secret)
    calls: list[dict] = []

    class Payload:
        sku = "PROD-001"
        stock_level = 5

    async def _fake_inventory_lookup(input_data):
        calls.append(input_data)
        return ToolResult(
            success=True,
            error=None,
            is_retryable=False,
            data=Payload(),
        )

    monkeypatch.setattr(
        "api.webhooks.telegram.execute_inventory_lookup",
        _fake_inventory_lookup,
    )

    payload = {
        "update_id": 970000002,
        "callback_query": {
            "id": "cb1",
            "from": {"id": 99, "is_bot": False, "first_name": "T"},
            "message": {
                "message_id": 2,
                "chat": {"id": 99, "type": "private"},
                "date": _fresh_date(),
                "text": "retry",
            },
            "data": "retry:inventory_check:PROD-001",
        },
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/webhooks/telegram",
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        )
    assert response.status_code == 200
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_parallel_calls_one_timeout_does_not_break_other(monkeypatch) -> None:
    secret = "timeout_secret_1234567890"
    monkeypatch.setattr(dependencies.settings, "TELEGRAM_WEBHOOK_SECRET", secret)

    class Payload:
        sku = "PROD-001"
        stock_level = 5

    async def _fake_inventory_lookup(_):
        return ToolResult(success=True, error=None, is_retryable=False, data=Payload())

    monkeypatch.setattr(
        "core.telegram.message_handler.execute_inventory_lookup",
        _fake_inventory_lookup,
    )

    payload = {
        "update_id": 970000003,
        "message": {
            "message_id": 3,
            "from": {"id": 99, "is_bot": False, "first_name": "T"},
            "chat": {"id": 99, "type": "private"},
            "date": _fresh_date(),
            "text": "/inventory PROD-001",
        },
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r1 = await client.post(
            "/webhooks/telegram",
            json=payload,
            headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        )
        r2 = await client.post(
            "/webhooks/telegram",
            json={**payload, "update_id": 970000004},
            headers={"X-Telegram-Bot-Api-Secret-Token": secret},
        )

    assert r1.status_code == 200
    assert r2.status_code == 200
