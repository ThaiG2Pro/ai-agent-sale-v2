"""Telegram message handler - processes messages with LangGraph agent (T037-T042).

Handles incoming Telegram messages and callback queries by:
1. Extracting chat_id and message text
2. Creating LangGraph session_id from chat_id
3. Invoking the agent with proper AgentState (session_id/customer_id/user_message)
4. Extracting response text (including HITL-paused acknowledgement)
5. Sending response back to Telegram
6. Dispatching post-turn memory background tasks
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from langgraph.errors import GraphInterrupt

from core.agent.graph import build_graph, make_agent_config
from core.agent.state import make_initial_state
from core.agent.tools import execute_inventory_lookup
from services.database import AsyncSessionLocal
from services.telegram_service import create_retry_keyboard, send_telegram_message

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from core.telegram.models import TelegramUpdate

logger = logging.getLogger(__name__)

_HITL_PENDING_MESSAGE = (
    "Yêu cầu đặt hàng của bạn đang chờ xác nhận từ nhân viên. "
    "Chúng tôi sẽ phản hồi sớm nhất có thể. Cảm ơn bạn đã kiên nhẫn!"
)


async def process_telegram_message(
    update: TelegramUpdate,
    chat_id: int,
    graph: CompiledStateGraph | None = None,
) -> None:
    """Process Telegram message with LangGraph agent (T037-T042).

    Args:
        update: Telegram update payload
        chat_id: Telegram chat/user ID
        graph: Compiled agent graph with checkpointer (shared, stateless — see
            api.dependencies.get_agent_graph). If omitted (e.g. called directly
            in tests), a fresh uncheckpointed graph is built; HITL pause/resume
            across separate calls will not work on that fallback path.

    Flow:
        1. Extract message text (T038)
        2. Create session_id: telegram_{chat_id} (T038)
        3. Check for an in-flight HITL pause (queue message instead of re-invoking)
        4. Invoke LangGraph agent with proper AgentState (T039)
        5. Extract response text, including HITL-paused acknowledgement (T040)
        6. Send response to Telegram (T041)
        7. Dispatch post-turn memory background tasks
        8. Log processing (T042)
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

        session_id = f"telegram_{chat_id}"
        customer_id = str(chat_id)

        logger.info(
            "Processing message",
            extra={
                "chat_id": chat_id,
                "session_id": session_id,
                "text_length": len(text),
            },
        )

        is_hitl_paused = False
        success = False

        async with AsyncSessionLocal() as db:
            # Paused-session gateway — queue the message instead of re-invoking
            # a thread that's currently interrupted awaiting admin review.
            from api.dependencies import check_paused_session

            pause_info = await check_paused_session(session_id, text, db)
            if pause_info["queued"]:
                await send_telegram_message(chat_id, pause_info["message"])
                return

            # v3-0 P3 (T09 3.3): backpressure — cap concurrent LLM turns
            # in-process. On overflow the message goes to queued_messages and
            # the customer gets the holding message instead of a hung turn.
            from core.config import settings as _settings

            if _settings.RESILIENCE_V3_ENABLED:
                from services import resilience

                sem = resilience.turn_semaphore()
                if sem.locked():
                    from models.schema import QueuedMessage

                    db.add(QueuedMessage(session_id=session_id, message_text=text))
                    await db.commit()
                    resilience.record_degraded_turn()
                    await send_telegram_message(chat_id, resilience.holding_message())
                    return
                await sem.acquire()
            else:
                sem = None

            active_graph = graph if graph is not None else build_graph()
            config = make_agent_config(session_id, db=db)
            initial_state = make_initial_state(
                text,
                session_id=session_id,
                customer_id=customer_id,
            )

            # Tag OpenInference spans with session/user so Telegram turns show
            # up in Phoenix's Sessions view like API turns do.
            from openinference.instrumentation import using_attributes

            try:
                with using_attributes(session_id=session_id, user_id=customer_id):
                    final_state = await active_graph.ainvoke(initial_state, config=config)
            finally:
                if sem is not None:
                    sem.release()

            # Detect HITL pause: aget_state().next is non-empty when interrupt() fired.
            snapshot = await active_graph.aget_state(config)
            is_hitl_paused = bool(snapshot.next)

            if is_hitl_paused:
                response_text = _HITL_PENDING_MESSAGE
            else:
                response_text = final_state.get("response") or (
                    "Sorry, I couldn't process your request. Please try again."
                )

            success = await send_telegram_message(chat_id, response_text)

            if not success:
                logger.error(
                    "Failed to send response to Telegram",
                    extra={"chat_id": chat_id, "response_length": len(response_text)},
                )

            if not is_hitl_paused:
                from services.memory.background import post_turn_tasks

                task = asyncio.create_task(
                    post_turn_tasks(
                        customer_id=customer_id,
                        thread_id=session_id,
                        state=final_state,
                        db_factory=AsyncSessionLocal,
                    )
                )
                task.add_done_callback(
                    lambda t: (
                        logger.error("Post-turn background tasks failed: %s", t.exception())
                        if t.exception()
                        else None
                    )
                )

        logger.info(
            "Telegram message processing complete",
            extra={
                "chat_id": chat_id,
                "session_id": session_id,
                "success": success,
                "hitl_paused": is_hitl_paused,
            },
        )

    except GraphInterrupt:
        # Defensive catch: LangGraph normally suppresses GraphInterrupt inside
        # ainvoke, but in rare edge cases it may propagate here instead.
        await send_telegram_message(chat_id, _HITL_PENDING_MESSAGE)

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
