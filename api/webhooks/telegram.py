"""Telegram webhook endpoint (T028-T033).

Receives webhook updates from Telegram Bot API and processes them asynchronously.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: TC002

from api.dependencies import get_agent_graph, verify_telegram_secret
from api.webhooks import hitl_callbacks
from core.agent.tools import execute_inventory_lookup
from core.telegram import security as telegram_security
from core.telegram.message_handler import process_telegram_message
from core.telegram.models import TelegramUpdate  # noqa: TC001
from services.database import get_db
from services.telegram_service import (
    check_duplicate_update,
    create_retry_keyboard,
    record_telegram_update,
    send_telegram_message,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Strong references to in-flight background tasks. asyncio only keeps weak
# references to tasks — without this set a task can be garbage-collected
# mid-execution and silently never complete.
_background_tasks: set[asyncio.Task] = set()


@router.post("/telegram")
async def telegram_webhook(
    update: TelegramUpdate,
    db: Annotated[AsyncSession, Depends(get_db)],
    graph: Annotated[Any, Depends(get_agent_graph)],
    _: Annotated[None, Depends(verify_telegram_secret)],
) -> dict:
    """Handle incoming Telegram webhook updates (T028-T033).

    This endpoint:
    1. Validates the webhook secret token (will be added in Phase 4)
    2. Checks for duplicate updates
    3. Records the update in the database
    4. Returns 200 OK acknowledgment within 200ms
    5. Processes the message in the background

    Args:
        update: Telegram update payload (parsed via Pydantic)
        background_tasks: FastAPI background task scheduler
        db: Database session
        x_telegram_bot_api_secret_token: Secret token from Telegram (header)

    Returns:
        {"ok": true} acknowledgment

    Note: Security validation (secret token check) will be added in Phase 4 (T049-T056)
    """
    update_id = update.update_id
    try:
        telegram_security.validate_message_timestamp(update)
    except Exception:
        logger.warning(
            "Telegram webhook rejected by timestamp validation",
            extra={"update_id": update_id},
            exc_info=True,
        )
        raise
    chat_id = update.get_chat_id()
    callback_data = update.callback_query.data if update.callback_query else None
    if callback_data and callback_data.startswith("retry:"):
        await _handle_retry_callback(update, chat_id)
        return {"ok": True}

    # v3-0 P2 (T13): admin review buttons + force-reply reasons. Handled
    # BEFORE the agent flow so admin messages never invoke the sales agent.
    if callback_data and callback_data.startswith("hitl:"):
        if await hitl_callbacks.handle_hitl_callback(update, db, graph):
            return {"ok": True}
    if chat_id is not None and chat_id == hitl_callbacks.admin_chat_id():
        if await hitl_callbacks.handle_admin_reason_reply(update, db, graph):
            return {"ok": True}
        # Guard: plain admin-chat chatter is not customer traffic — ignore it
        # instead of running the sales agent on it.
        logger.info(
            "Ignoring non-review message from admin chat",
            extra={"update_id": update_id, "chat_id": chat_id},
        )
        return {"ok": True}

    if chat_id is None:
        logger.warning("Received update without chat_id", extra={"update_id": update_id})
        return {"ok": True}  # Acknowledge even if we can't process

    # T031: Check for duplicate update
    is_duplicate = await check_duplicate_update(db, update_id)
    if is_duplicate:
        logger.info(
            "Duplicate update ignored",
            extra={"update_id": update_id, "chat_id": chat_id},
        )
        return {"ok": True}  # Acknowledge duplicate without re-processing

    # T032: Record update in database
    message_id = update.get_message_id()
    message_type = "callback_query" if update.callback_query else "message"
    raw_payload = update.to_json_dict()

    await record_telegram_update(
        db,
        update_id=update_id,
        chat_id=chat_id,
        message_id=message_id,
        message_type=message_type,
        raw_payload=raw_payload,
    )

    # T043-T045: Schedule background processing with exception handling
    async def _process_with_error_handling():
        """Wrapper to catch and log background task exceptions (T044-T045)."""
        try:
            await process_telegram_message(update, chat_id, graph=graph)
        except Exception as e:
            logger.error(
                "Background task error",
                extra={
                    "update_id": update_id,
                    "chat_id": chat_id,
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )

    task = asyncio.create_task(_process_with_error_handling())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    logger.info(
        "Telegram webhook acknowledged",
        extra={
            "update_id": update_id,
            "chat_id": chat_id,
            "message_type": message_type,
        },
    )

    # T033: Return 200 OK within 200ms
    return {"ok": True}


async def _handle_retry_callback(update: TelegramUpdate, chat_id: int | None) -> None:
    """Handle retry callback from inline keyboard button."""
    if chat_id is None or update.callback_query is None or not update.callback_query.data:
        return
    callback_data = update.callback_query.data
    parts = callback_data.split(":")
    if len(parts) < 2:
        return
    tool_name = parts[1]
    context = parts[2] if len(parts) > 2 else ""
    if tool_name == "inventory_check":
        sku = context or "PROD-001"
        result = await execute_inventory_lookup(sku)
        if result.success:
            payload = result.data
            await send_telegram_message(
                chat_id,
                f"Inventory check completed. SKU: {payload.sku}, stock: {payload.stock_level}",
            )
        else:
            await send_telegram_message(
                chat_id,
                result.error or "Tool retry failed",
                reply_markup=create_retry_keyboard(tool_name, context),
            )
