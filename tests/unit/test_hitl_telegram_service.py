"""Unit tests for services/hitl/telegram_service.py (T071).

Covers the retry/backoff contract of TelegramService.send_telegram_message:
success, missing token, 429 rate-limit retry, hard API error, and network
failures exhausting retries. All HTTP mocked via respx; sleeps patched out.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest
import respx

from core.config import settings
from services.hitl.telegram_service import TelegramService

SEND_URL = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"


@pytest.mark.asyncio
async def test_send_skips_when_token_not_configured():
    with patch.object(settings, "TELEGRAM_BOT_TOKEN", "your_bot_token_here"):
        ok = await TelegramService.send_telegram_message("123", "hi")
    assert ok is False


@pytest.mark.asyncio
@respx.mock
async def test_send_success_returns_true():
    route = respx.post(SEND_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    ok = await TelegramService.send_telegram_message("123", "hi")
    assert ok is True
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_send_retries_on_rate_limit_then_succeeds():
    route = respx.post(SEND_URL).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    with patch("services.hitl.telegram_service.asyncio.sleep", AsyncMock()) as mock_sleep:
        ok = await TelegramService.send_telegram_message("123", "hi")
    assert ok is True
    assert route.call_count == 2
    mock_sleep.assert_awaited_once_with(1)


@pytest.mark.asyncio
@respx.mock
async def test_send_hard_api_error_returns_false_without_retry():
    route = respx.post(SEND_URL).mock(return_value=httpx.Response(400, text="bad request"))
    ok = await TelegramService.send_telegram_message("123", "hi")
    assert ok is False
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_send_network_error_exhausts_retries():
    route = respx.post(SEND_URL).mock(side_effect=httpx.ConnectError("refused"))
    with patch("services.hitl.telegram_service.asyncio.sleep", AsyncMock()):
        ok = await TelegramService.send_telegram_message("123", "hi")
    assert ok is False
    assert route.call_count == 3  # max_retries
