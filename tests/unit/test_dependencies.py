"""Unit tests for API dependencies."""

from __future__ import annotations

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
