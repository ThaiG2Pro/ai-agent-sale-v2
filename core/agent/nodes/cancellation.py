"""cancellation_node — processes order cancellation.

Why: Handles the path where a customer decides to cancel their order,
specifically overriding any previous admin approval if requested.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langgraph.types import Command

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

    from core.agent.state import AgentState

logger = logging.getLogger(__name__)


async def cancellation_node(state: AgentState, config: RunnableConfig | None = None) -> Command:
    """Processes order cancellation (Phase 12).

    1. Updates order status to 'cancelled' in DB and state.
    2. Composes a polite cancellation message in Vietnamese.
    3. Routes to answer_node for final response.
    """
    session_id = state.get("session_id")
    order_info = state.get("order_info")

    logger.info(f"Processing cancellation for session {session_id}")

    if config:
        db = config.get("configurable", {}).get("db")
        if db and session_id:
            try:
                from sqlalchemy import update

                from models.schema import HITLMetadata, Order

                await db.execute(
                    update(Order).where(Order.session_id == session_id).values(status="cancelled")
                )
                await db.execute(
                    update(HITLMetadata)
                    .where(HITLMetadata.session_id == session_id)
                    .values(status="cancelled")
                )
            except Exception as e:
                logger.warning("Failed to update DB records on cancellation: %s", e)

    # Composition of polite Vietnamese cancellation message
    message = (
        "Dạ, yêu cầu hủy đơn hàng của anh/chị đã được hệ thống ghi nhận và thực hiện hủy thành công ạ. "
        "Nếu anh/chị có nhu cầu tham khảo sản phẩm nào khác hoặc cần hỗ trợ, cứ nhắn cho shop bất cứ lúc nào nhé!"
    )

    update_payload: dict = {"response": message}

    if order_info and isinstance(order_info, dict):
        new_order_info = {**order_info, "status": "cancelled"}
        update_payload["order_info"] = new_order_info

    return Command(
        goto="answer_node",
        update=update_payload,
    )
