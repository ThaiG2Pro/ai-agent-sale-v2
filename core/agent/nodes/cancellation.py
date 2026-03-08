"""cancellation_node — processes order cancellation.

Why: Handles the path where a customer decides to cancel their order,
specifically overriding any previous admin approval if requested.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langgraph.types import Command

from core.config import settings

if TYPE_CHECKING:
    from core.agent.state import AgentState

logger = logging.getLogger(__name__)


async def cancellation_node(state: AgentState) -> Command:
    """Processes order cancellation (Phase 12).

    1. Updates order status to 'cancelled' in state.
    2. Composes a polite cancellation message.
    3. Routes to answer_node for final response.
    """
    session_id = state.get("session_id")
    order_info = state.get("order_info")

    logger.info(f"Processing cancellation for session {session_id}")

    # Composition of cancellation message
    message = (
        "Your order has been cancelled as requested. "
        f"If you need further assistance, please contact us at {settings.SUPPORT_CONTACT_LINK}."
    )

    update_payload: dict = {"response": message}

    if order_info:
        # Update order_info status if it exists
        new_order_info = {**order_info, "status": "cancelled"}
        update_payload["order_info"] = new_order_info

    return Command(
        goto="answer_node",
        update=update_payload,
    )
