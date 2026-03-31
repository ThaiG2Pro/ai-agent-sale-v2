"""Telegram message handler - processes messages with LangGraph agent (T037-T042).

Handles incoming Telegram messages and callback queries by:
1. Extracting chat_id and message text
2. Creating LangGraph thread_id from chat_id
3. Invoking the agent with message input
4. Extracting response text
5. Sending response back to Telegram
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from core.agent.graph import build_graph
from core.agent.tools import execute_inventory_lookup
from services.telegram_service import create_retry_keyboard, send_telegram_message

if TYPE_CHECKING:
    from core.agent.state import AgentState
    from core.telegram.models import TelegramUpdate

logger = logging.getLogger(__name__)


async def process_telegram_message(update: TelegramUpdate, chat_id: int) -> None:
    """Process Telegram message with LangGraph agent (T037-T042).

    Args:
        update: Telegram update payload
        chat_id: Telegram chat/user ID

    Flow:
        1. Extract message text (T038)
        2. Create thread_id: telegram_{chat_id} (T038)
        3. Invoke LangGraph agent (T039)
        4. Extract response text (T040)
        5. Send response to Telegram (T041)
        6. Log processing (T042)
    """
    logger.info(
        "Starting Telegram message processing",
        extra={"chat_id": chat_id, "update_id": update.update_id},
    )

    try:
        text = update.get_text()

        if text is None:
            logger.warning(
                "No text in message",
                extra={"chat_id": chat_id, "update_id": update.update_id},
            )
            await send_telegram_message(
                chat_id,
                "Sorry, I can only process text messages at the moment.",
            )
            return

        # US3: Direct inventory check path to demonstrate timeout + retry flow
        if text.startswith("/inventory "):
            sku = text.removeprefix("/inventory ").strip().upper() or "PROD-001"
            tool_result = await execute_inventory_lookup(sku)
            if tool_result.success:
                payload = tool_result.data
                await send_telegram_message(
                    chat_id,
                    f"Inventory: {payload.sku} has {payload.stock_level} units available.",
                )
            else:
                reply_markup = (
                    create_retry_keyboard("inventory_check", sku)
                    if tool_result.is_retryable
                    else None
                )
                await send_telegram_message(
                    chat_id,
                    tool_result.error or "Inventory tool failed.",
                    reply_markup=reply_markup,
                )
            return

        thread_id = f"telegram_{chat_id}"

        logger.info(
            "Processing message",
            extra={
                "chat_id": chat_id,
                "thread_id": thread_id,
                "text_length": len(text),
            },
        )

        graph = build_graph()
        config = {"configurable": {"thread_id": thread_id}}

        initial_state: AgentState = {
            "messages": [{"role": "user", "content": text}],
            "chat_id": chat_id,
            "update_id": update.update_id,
        }

        result = await graph.ainvoke(initial_state, config)

        response_text = _extract_response_from_state(result)

        if not response_text:
            logger.error(
                "No response from agent",
                extra={"chat_id": chat_id, "state": result},
            )
            response_text = "Sorry, I couldn't process your request. Please try again."

        success = await send_telegram_message(chat_id, response_text)

        if not success:
            logger.error(
                "Failed to send response to Telegram",
                extra={"chat_id": chat_id, "response_length": len(response_text)},
            )

        logger.info(
            "Telegram message processing complete",
            extra={
                "chat_id": chat_id,
                "thread_id": thread_id,
                "success": success,
                "response_length": len(response_text) if success else 0,
            },
        )

    except Exception as e:
        logger.error(
            "Error processing Telegram message",
            extra={
                "chat_id": chat_id,
                "error": str(e),
                "error_type": type(e).__name__,
            },
            exc_info=True,
        )

        try:
            await send_telegram_message(
                chat_id,
                "Sorry, an error occurred while processing your message. Please try again later.",
            )
        except Exception as send_error:
            logger.error(
                "Failed to send error message to user",
                extra={"chat_id": chat_id, "error": str(send_error)},
            )


def _extract_response_from_state(state: dict[str, Any]) -> str:
    """Extract final response text from LangGraph state (T040).

    Args:
        state: LangGraph agent state after execution

    Returns:
        Response text to send to user
    """
    messages = state.get("messages", [])

    if not messages:
        return ""

    for message in reversed(messages):
        if isinstance(message, dict):
            role = message.get("role")
            content = message.get("content")
            if role == "assistant" and content:
                return str(content)
        elif hasattr(message, "role") and hasattr(message, "content"):
            if message.role == "assistant" and message.content:
                return str(message.content)

    return ""
