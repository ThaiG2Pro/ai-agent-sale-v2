"""Unit tests for HITL timeout scheduler customer notification (FR-016, P0-3)."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.hitl.timeout_scheduler import (
    TIMEOUT_WARN_MESSAGE_VI,
    _process_timeouts,
    _telegram_chat_id_from_session,
)


def test_telegram_chat_id_extraction():
    assert _telegram_chat_id_from_session("telegram_12345") == 12345
    assert _telegram_chat_id_from_session("telegram_-100123") == -100123  # group chat
    assert _telegram_chat_id_from_session("api_session_abc") is None
    assert _telegram_chat_id_from_session("telegram_abc") is None
    assert _telegram_chat_id_from_session("telegram_") is None


def _make_meta(session_id: str) -> MagicMock:
    meta = MagicMock()
    meta.session_id = session_id
    meta.paused_at = datetime.now(UTC) - timedelta(minutes=45)
    meta.timeout_notified_at = None
    return meta


def _make_db(to_warn: list, to_escalate: list) -> AsyncMock:
    """Mock DB whose first execute() serves the warn query, second the escalate query."""
    db = AsyncMock()
    warn_result = MagicMock()
    warn_result.scalars.return_value.all.return_value = to_warn
    escalate_result = MagicMock()
    escalate_result.scalars.return_value.all.return_value = to_escalate
    db.execute.side_effect = [warn_result, escalate_result]
    return db


@pytest.mark.asyncio
async def test_warn_sends_telegram_to_customer():
    """30' timeout on a Telegram session → real sendMessage to the customer."""
    meta = _make_meta("telegram_999")
    db = _make_db(to_warn=[meta], to_escalate=[])

    with patch(
        "services.telegram_service.send_telegram_message",
        new=AsyncMock(return_value=True),
    ) as mock_send:
        await _process_timeouts(db)

    mock_send.assert_awaited_once_with(999, TIMEOUT_WARN_MESSAGE_VI)
    assert meta.timeout_notified_at is not None


@pytest.mark.asyncio
async def test_warn_non_telegram_session_skips_send_with_warning(caplog):
    """Sessions without a Telegram chat_id log a clear warning instead of sending."""
    meta = _make_meta("api_session_1")
    db = _make_db(to_warn=[meta], to_escalate=[])

    with patch(
        "services.telegram_service.send_telegram_message",
        new=AsyncMock(return_value=True),
    ) as mock_send:
        with caplog.at_level("WARNING"):
            await _process_timeouts(db)

    mock_send.assert_not_awaited()
    assert any("no Telegram chat_id" in rec.getMessage() for rec in caplog.records)
    # Still marked notified so the scheduler doesn't retry a channel-less session forever
    assert meta.timeout_notified_at is not None


@pytest.mark.asyncio
async def test_warn_send_failure_still_marks_notified(caplog):
    """Telegram send failure is logged; timeout_notified_at still set (no spam loop)."""
    meta = _make_meta("telegram_777")
    db = _make_db(to_warn=[meta], to_escalate=[])

    with patch(
        "services.telegram_service.send_telegram_message",
        new=AsyncMock(return_value=False),
    ):
        with caplog.at_level("WARNING"):
            await _process_timeouts(db)

    assert any("Telegram send failed" in rec.getMessage() for rec in caplog.records)
    assert meta.timeout_notified_at is not None
