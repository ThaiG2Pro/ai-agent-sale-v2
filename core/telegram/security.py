"""Security validation helpers for Telegram webhooks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

if TYPE_CHECKING:
    from core.telegram.models import TelegramUpdate

MAX_MESSAGE_AGE = timedelta(minutes=5)


def validate_message_timestamp(update: TelegramUpdate) -> None:
    """Reject replayed updates older than 5 minutes."""
    message_ts = None
    if update.message is not None:
        message_ts = update.message.date
    elif update.callback_query is not None and update.callback_query.message is not None:
        message_ts = update.callback_query.message.date

    if message_ts is None:
        return

    message_time = datetime.fromtimestamp(message_ts, tz=UTC)
    now = datetime.now(UTC)
    if now - message_time > MAX_MESSAGE_AGE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Message timestamp outside acceptable window (>5 minutes old)",
        )
