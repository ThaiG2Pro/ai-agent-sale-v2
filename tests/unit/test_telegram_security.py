"""Unit tests for Telegram timestamp replay protection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from core.telegram.models import TelegramUpdate
from core.telegram.security import validate_message_timestamp


def _build_update(ts: int) -> TelegramUpdate:
    return TelegramUpdate(
        update_id=1111,
        message={
            "message_id": 1,
            "from": {"id": 1, "is_bot": False, "first_name": "Test"},
            "chat": {"id": 1, "type": "private"},
            "date": ts,
            "text": "hello",
        },
    )


def test_validate_message_timestamp_accepts_fresh() -> None:
    now_ts = int(datetime.now(UTC).timestamp())
    update = _build_update(now_ts)
    validate_message_timestamp(update)


def test_validate_message_timestamp_rejects_old() -> None:
    old_ts = int((datetime.now(UTC) - timedelta(minutes=6)).timestamp())
    update = _build_update(old_ts)
    with pytest.raises(HTTPException) as exc:
        validate_message_timestamp(update)
    assert exc.value.status_code == 403
