"""
Why this exists: External integration for Telegram notifications (Article III).
What it does: Sends messages to support chat via Telegram Bot API.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from core.config import settings

logger = logging.getLogger(__name__)


class TelegramService:
    @staticmethod
    async def send_telegram_message(chat_id: str, message_text: str) -> bool:
        """Sends a message via Telegram Bot API with retry logic (T071)."""
        token = getattr(settings, "TELEGRAM_BOT_TOKEN", None)
        if not token or token == "your_bot_token_here":
            logger.warning("TELEGRAM_BOT_TOKEN not configured, skipping notification.")
            return False

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message_text, "parse_mode": "HTML"}

        max_retries = 3
        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        return True
                    if resp.status_code == 429:
                        retry_after = int(resp.headers.get("Retry-After", 5))
                        logger.warning(f"Telegram rate limited. Retrying after {retry_after}s")
                        await asyncio.sleep(retry_after)
                        continue

                    logger.error(f"Telegram API error: {resp.status_code} - {resp.text}")
                    return False
            except Exception as e:
                logger.error(f"Failed to send Telegram message (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2**attempt)
                else:
                    return False

        return False
