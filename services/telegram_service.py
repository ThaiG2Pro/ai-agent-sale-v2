"""Telegram webhook service layer (T024-T025, T034-T035).

Handles:
- Deduplication check for incoming Telegram updates
- Recording updates in database
- Sending messages back to Telegram
"""

import asyncio
import logging
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from core.config import settings
from models.telegram_updates import TelegramUpdate

logger = logging.getLogger(__name__)


async def check_duplicate_update(db: AsyncSession, update_id: int) -> bool:
    """Check if this update_id has already been processed (T024).

    Args:
        db: Database session
        update_id: Telegram's unique update identifier

    Returns:
        True if duplicate (already exists), False if new update
    """
    stmt = select(TelegramUpdate.id).where(TelegramUpdate.update_id == update_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def record_telegram_update(
    db: AsyncSession,
    update_id: int,
    chat_id: int,
    message_id: int | None,
    message_type: str,
    raw_payload: dict,
) -> TelegramUpdate | None:
    """Record a new Telegram update in the database (T025).

    Args:
        db: Database session
        update_id: Telegram's unique update identifier
        chat_id: Telegram chat/user ID
        message_id: Telegram message ID (None for callback queries)
        message_type: Type of update ('text', 'callback_query', etc.)
        raw_payload: Full Telegram update payload for debugging

    Returns:
        TelegramUpdate object if successful, None if duplicate (IntegrityError)
    """
    try:
        update = TelegramUpdate(
            update_id=update_id,
            chat_id=chat_id,
            message_id=message_id,
            message_type=message_type,
            raw_payload=raw_payload,
        )
        db.add(update)
        await db.commit()
        await db.refresh(update)
        logger.info(
            "Recorded Telegram update",
            extra={
                "update_id": update_id,
                "chat_id": chat_id,
                "message_type": message_type,
            },
        )
        return update
    except IntegrityError as e:
        await db.rollback()
        logger.warning(
            "Duplicate Telegram update ignored",
            extra={"update_id": update_id, "error": str(e)},
        )
        return None


async def send_telegram_message(
    chat_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> bool:
    """Send a message to Telegram user via Bot API (T034-T035).

    Args:
        chat_id: Telegram chat/user ID to send message to
        text: Message text to send
        reply_markup: Optional inline keyboard or reply markup

    Returns:
        True if sent successfully, False otherwise

    Retry logic: 3 attempts with exponential backoff (1s, 2s, 4s)
    """
    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    max_retries = 3
    base_delay = 1.0

    async with httpx.AsyncClient(timeout=10.0) as client:
        for attempt in range(max_retries):
            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()

                logger.info(
                    "Sent Telegram message",
                    extra={
                        "chat_id": chat_id,
                        "text_length": len(text),
                        "attempt": attempt + 1,
                    },
                )
                return True

            except httpx.HTTPStatusError as e:
                logger.error(
                    "Telegram API HTTP error",
                    extra={
                        "chat_id": chat_id,
                        "status_code": e.response.status_code,
                        "response": e.response.text,
                        "attempt": attempt + 1,
                    },
                )
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    logger.info(f"Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                else:
                    return False

            except Exception as e:
                logger.error(
                    "Telegram send error",
                    extra={
                        "chat_id": chat_id,
                        "error": str(e),
                        "attempt": attempt + 1,
                    },
                )
                if attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    await asyncio.sleep(delay)
                else:
                    return False

    return False


def create_retry_keyboard(tool_name: str, context: str | None = None) -> dict[str, Any]:
    """Create Telegram inline keyboard with retry action."""
    callback_data = f"retry:{tool_name}"
    if context:
        callback_data = f"{callback_data}:{context}"
    return {
        "inline_keyboard": [
            [{"text": "🔄 Retry", "callback_data": callback_data}],
            [{"text": "❌ Cancel", "callback_data": "cancel"}],
        ]
    }
