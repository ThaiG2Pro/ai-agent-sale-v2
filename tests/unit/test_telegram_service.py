"""Unit tests for telegram_service.py (T026-T027, T036)."""

import time
from unittest.mock import AsyncMock

import httpx
import pytest

from models.telegram_updates import TelegramUpdate
from services.telegram_service import (
    check_duplicate_update,
    record_telegram_update,
    send_telegram_message,
)


def _unique_id():
    """Generate unique update_id for tests."""
    return int(time.time() * 1000000) % 999999999


@pytest.mark.asyncio
async def test_check_duplicate_update_returns_false_for_new_update(db_session):
    """Test check_duplicate_update returns False for new update_id (T026)."""
    update_id = _unique_id()

    is_duplicate = await check_duplicate_update(db_session, update_id)

    assert is_duplicate is False


@pytest.mark.asyncio
async def test_check_duplicate_update_returns_true_for_existing_update(db_session):
    """Test check_duplicate_update returns True for existing update_id (T026)."""
    update_id = _unique_id()
    chat_id = 111222333

    # Insert update first
    update = TelegramUpdate(
        update_id=update_id,
        chat_id=chat_id,
        message_type="text",
        raw_payload={"test": "data"},
    )
    db_session.add(update)
    await db_session.commit()

    # Check if duplicate
    is_duplicate = await check_duplicate_update(db_session, update_id)

    assert is_duplicate is True


@pytest.mark.asyncio
async def test_record_telegram_update_success(db_session):
    """Test successful recording of new Telegram update (T025)."""
    update_id = _unique_id()
    chat_id = 444555666
    message_id = 789
    message_type = "text"
    raw_payload = {"update_id": update_id, "message": {"text": "Hello"}}

    result = await record_telegram_update(
        db_session,
        update_id,
        chat_id,
        message_id,
        message_type,
        raw_payload,
    )

    assert result is not None
    assert result.update_id == update_id
    assert result.chat_id == chat_id
    assert result.message_id == message_id
    assert result.message_type == message_type
    assert result.raw_payload == raw_payload


@pytest.mark.asyncio
async def test_record_telegram_update_handles_duplicate(db_session):
    """Test duplicate update_id raises IntegrityError and returns None (T027)."""
    update_id = _unique_id()
    chat_id = 111111111

    # Insert first update
    first_result = await record_telegram_update(
        db_session,
        update_id,
        chat_id,
        None,
        "text",
        {"test": "first"},
    )
    assert first_result is not None

    # Attempt duplicate insert
    second_result = await record_telegram_update(
        db_session,
        update_id,
        chat_id + 1,  # Different chat_id, same update_id
        None,
        "text",
        {"test": "duplicate"},
    )

    # Should return None (duplicate handled)
    assert second_result is None


@pytest.mark.asyncio
async def test_send_telegram_message_success(respx_mock):
    """Test successful Telegram message sending (T036)."""
    chat_id = 123456789
    text = "Test message"

    # Mock Telegram API response - match any bot token
    respx_mock.post(url__regex=r"https://api\.telegram\.org/bot.*/sendMessage").mock(
        return_value=httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 1, "chat": {"id": chat_id}}},
        )
    )

    success = await send_telegram_message(chat_id, text)

    assert success is True


@pytest.mark.asyncio
async def test_send_telegram_message_retry_on_failure(respx_mock, monkeypatch):
    """Test retry logic on HTTP error (T036): succeeds once a retry gets a 200."""
    chat_id = 123456789
    text = "Test message"

    monkeypatch.setattr("services.telegram_service.asyncio.sleep", AsyncMock(return_value=None))

    # First two attempts fail, third succeeds
    respx_mock.post(url__regex=r"https://api\.telegram\.org/bot.*/sendMessage").mock(
        side_effect=[
            httpx.Response(429, json={"ok": False, "description": "Too Many Requests"}),
            httpx.Response(429, json={"ok": False, "description": "Too Many Requests"}),
            httpx.Response(200, json={"ok": True, "result": {"message_id": 1}}),
        ]
    )

    success = await send_telegram_message(chat_id, text)

    assert success is True
    assert respx_mock.calls.call_count == 3


@pytest.mark.asyncio
async def test_send_telegram_message_failure_after_retries(respx_mock):
    """Test failure after all retries exhausted (T036)."""
    chat_id = 123456789
    text = "Test message"

    # All attempts fail
    respx_mock.post(url__regex=r"https://api\.telegram\.org/bot.*/sendMessage").mock(
        side_effect=httpx.Response(
            500,
            json={"ok": False, "description": "Internal Server Error"},
        )
    )

    success = await send_telegram_message(chat_id, text)

    assert success is False
