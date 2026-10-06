"""cancellation_node — processes order cancellation.

Why: Handles the path where a customer decides to cancel their order. Cancelling is
destructive, so it is scoped: only orders that are not yet confirmed (drafts awaiting
review, a paused HITL review, a half-filled pending order) are cancelled automatically.
A CONFIRMED order has already decremented stock and may be paid — that goes to a human.
What: Cancels active drafts + paused reviews, commits, and reports what actually
happened (never claims success when the DB write failed or nothing was cancelled).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from langgraph.types import Command

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig

    from core.agent.state import AgentState

logger = logging.getLogger(__name__)

CANCELLED_MESSAGE = (
    "Dạ, yêu cầu hủy đơn hàng của anh/chị đã được hệ thống ghi nhận và thực hiện hủy "
    "thành công ạ. Nếu anh/chị có nhu cầu tham khảo sản phẩm nào khác hoặc cần hỗ trợ, "
    "cứ nhắn cho shop bất cứ lúc nào nhé!"
)
NOTHING_TO_CANCEL_MESSAGE = (
    "Dạ, hiện anh/chị chưa có đơn hàng nào đang chờ xử lý để hủy ạ. "
    "Anh/chị cần shop hỗ trợ thêm gì không ạ?"
)


async def cancellation_node(state: AgentState, config: RunnableConfig | None = None) -> Command:
    """Processes order cancellation (Phase 12).

    1. Confirmed order in state or DB → customer_support_node (human cancels:
       restock / refund), nothing is cancelled automatically.
    2. Otherwise cancel active drafts (draft / pending_review) and paused HITL
       reviews for this session, commit.
    3. Respond with what happened; a DB failure hands off to support.
    """
    from services.draft_orders import ACTIVE_DRAFT_STATUSES

    session_id = state.get("session_id")
    order_info = state.get("order_info")
    db = ((config or {}).get("configurable") or {}).get("db")

    logger.info("Processing cancellation for session %s", session_id)

    state_has_open_order = bool(
        isinstance(order_info, dict)
        and order_info.get("status") not in ("cancelled", "expired", "confirmed")
    ) or bool(state.get("pending_order"))
    state_has_confirmed = isinstance(order_info, dict) and order_info.get("status") == "confirmed"

    cancelled_rows = 0
    if db is not None and session_id:
        try:
            from sqlalchemy import select, update

            from models.schema import HITLMetadata, Order

            confirmed = (
                await db.execute(
                    select(Order.id)
                    .where(Order.session_id == session_id, Order.status == "confirmed")
                    .limit(1)
                )
            ).scalar_one_or_none()
            if confirmed is not None or state_has_confirmed:
                return Command(
                    goto="customer_support_node",
                    update={
                        "hitl_rejection_reason": "cancel_confirmed_order",
                        "pending_order": None,
                    },
                )

            res = await db.execute(
                update(Order)
                .where(Order.session_id == session_id, Order.status.in_(ACTIVE_DRAFT_STATUSES))
                .values(status="cancelled")
            )
            cancelled_rows += res.rowcount or 0
            res = await db.execute(
                update(HITLMetadata)
                .where(
                    HITLMetadata.session_id == session_id,
                    HITLMetadata.status.in_(["paused", "resuming"]),
                )
                .values(status="cancelled")
            )
            cancelled_rows += res.rowcount or 0
            await db.commit()
        except Exception:
            logger.error("Cancellation DB update failed for %s", session_id, exc_info=True)
            try:
                await db.rollback()
            except Exception:
                pass
            return Command(
                goto="customer_support_node",
                update={"hitl_rejection_reason": "cancel_failed", "pending_order": None},
            )
    elif state_has_confirmed:
        return Command(
            goto="customer_support_node",
            update={"hitl_rejection_reason": "cancel_confirmed_order", "pending_order": None},
        )

    did_cancel = cancelled_rows > 0 or state_has_open_order
    update_payload: dict = {
        "response": CANCELLED_MESSAGE if did_cancel else NOTHING_TO_CANCEL_MESSAGE,
        "pending_order": None,
    }
    if isinstance(order_info, dict) and state_has_open_order:
        update_payload["order_info"] = {**order_info, "status": "cancelled"}

    return Command(goto="answer_node", update=update_payload)
