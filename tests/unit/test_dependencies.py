"""Unit tests for API dependencies."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from api import dependencies


@pytest.mark.asyncio
async def test_verify_telegram_secret_accepts_valid(monkeypatch) -> None:
    monkeypatch.setattr(
        dependencies.settings,
        "TELEGRAM_WEBHOOK_SECRET",
        "valid_secret_1234567890",
    )
    await dependencies.verify_telegram_secret("valid_secret_1234567890")


@pytest.mark.asyncio
async def test_verify_telegram_secret_rejects_missing(monkeypatch) -> None:
    monkeypatch.setattr(
        dependencies.settings,
        "TELEGRAM_WEBHOOK_SECRET",
        "valid_secret_1234567890",
    )
    with pytest.raises(HTTPException) as exc:
        await dependencies.verify_telegram_secret(None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_telegram_secret_rejects_invalid(monkeypatch) -> None:
    monkeypatch.setattr(
        dependencies.settings,
        "TELEGRAM_WEBHOOK_SECRET",
        "valid_secret_1234567890",
    )
    with pytest.raises(HTTPException) as exc:
        await dependencies.verify_telegram_secret("invalid")
    assert exc.value.status_code == 401


def test_blocking_statuses_cover_all_in_flight_states() -> None:
    """Gateway must block paused, resuming AND escalated; abandoned passes through."""
    assert dependencies.BLOCKING_STATUSES == frozenset({"paused", "resuming", "escalated"})
    assert "abandoned" not in dependencies.BLOCKING_STATUSES


@pytest.mark.asyncio
async def test_check_paused_session_queues_when_blocking() -> None:
    """A session in a blocking HITL status gets its message enqueued, not re-invoked."""
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = MagicMock()  # blocking record found
    db.execute.return_value = result

    with patch.object(
        dependencies.HITLService, "enqueue_message", new=AsyncMock()
    ) as mock_enqueue:
        outcome = await dependencies.check_paused_session("telegram_1", "hello", db)

    assert outcome["queued"] is True
    mock_enqueue.assert_awaited_once_with("telegram_1", "hello", db)


@pytest.mark.asyncio
async def test_check_paused_session_passes_when_no_blocking_record() -> None:
    """No blocking HITL record (e.g. abandoned/approved) → normal flow."""
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = None
    db.execute.return_value = result

    outcome = await dependencies.check_paused_session("telegram_1", "hello", db)

    assert outcome["queued"] is False
