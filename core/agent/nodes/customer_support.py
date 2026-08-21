"""customer_support_node — escalates session to human support.

Why: Final fallback for rejected orders, max escalation reached, or timeout.
What: Composes empathetic message via LLM and inserts into support queue.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from langgraph.types import Command
from sqlalchemy.dialects.postgresql import insert

from core.config import settings
from models.schema import HITLMetadata, SupportQueue

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.agent.state import AgentState

logger = logging.getLogger(__name__)

# v3-0 P2 (T06): customer-facing labels for internal reason slugs.
_REASON_LABELS = {
    "clarify_exhausted_still_ambiguous": (
        "shop cần thêm thông tin để chọn đúng sản phẩm cho bạn, nhân viên sẽ hỗ trợ trực tiếp"
    ),
    "high_risk_tier3": "đơn hàng cần nhân viên xác nhận trước khi xử lý",
    "max_escalation_reached": "yêu cầu cần nhân viên hỗ trợ trực tiếp",
}


async def customer_support_node(state: AgentState, config: RunnableConfig) -> Command:
    """Escalates session to human support (Phase 13).

    1. T040: Compose empathetic message using economy model.
    2. T041: Persist escalation to SupportQueue and update metadata.
    """
    db = cast("AsyncSession", config["configurable"].get("db"))
    session_id = state["session_id"]
    reason = state.get("hitl_rejection_reason", "Unable to process request")
    # v3-0 P2 (T06): internal reason slugs must never leak verbatim into the
    # customer-facing message — map known slugs to plain Vietnamese.
    reason = _REASON_LABELS.get(reason, reason)

    # --- T040: Empathetic Message ---
    try:
        system_prompt = (
            "Bạn là trợ lý bán hàng chu đáo, chân thành và thấu hiểu. "
            "Yêu cầu của khách hàng hiện chưa thể xử lý tự động được. "
            f"Hãy viết một phản hồi ngắn gọn, lịch sự và đồng cảm bằng tiếng Việt. "
            f"BẮT BUỘC nêu rõ lý do cụ thể cho khách (O27): '{reason}' — "
            "không được nói chung chung kiểu 'không thể xử lý'. "
            "Nếu phù hợp, gợi ý bước tiếp theo cho khách. "
            f"Hướng dẫn khách liên hệ bộ phận hỗ trợ khách hàng tại {settings.SUPPORT_CONTACT_LINK}."
        )

        from services.ai import AIGateway

        response = await AIGateway.complete(
            model="light-chat",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "Nhờ shop hỗ trợ giúp mình yêu cầu này."},
            ],
            temperature=0.3,
        )
        empathetic_message = response.choices[0].message.content
    except Exception as e:
        logger.error(f"Failed to compose empathetic message for {session_id}: {e}")
        empathetic_message = (
            f"Dạ rất xin lỗi bạn, hệ thống chưa thể xử lý hoàn tất yêu cầu: {reason}. "
            f"Bạn vui lòng liên hệ bộ phận hỗ trợ của shop tại đây để được hỗ trợ trực tiếp nhé: {settings.SUPPORT_CONTACT_LINK} 🙏"
        )

    # --- T041: SupportQueue Escalation ---
    try:
        # 1. Insert into SupportQueue (idempotent)
        snapshot = {
            "order_info": state.get("order_info"),
            "rejection_reason": reason,
            "last_messages": [
                {"role": m.type, "content": m.content} for m in state.get("messages", [])[-3:]
            ],
        }

        # v3-0 P2 (T07): the queue entry carries the standardized 4-part
        # handoff package instead of only a free-form snapshot. Best-effort.
        if settings.ORDER_HITL_V3_ENABLED:
            try:
                from services.hitl.handoff import build_handoff_package

                snapshot["handoff_package"] = await build_handoff_package(
                    db, dict(state), pause_reason=str(reason)
                )
            except Exception as pkg_exc:
                logger.warning("customer_support handoff package build failed: %s", pkg_exc)

        stmt = (
            insert(SupportQueue)
            .values(
                session_id=session_id,
                reason=reason[:50],
                context_snapshot=snapshot,
                status="pending",
            )
            .on_conflict_do_nothing(index_elements=["session_id"])
        )

        await db.execute(stmt)

        # 2. Update HITLMetadata status if it exists for this session
        pause_id = state.get("hitl_pause_id")
        if pause_id:
            from sqlalchemy import update

            await db.execute(
                update(HITLMetadata)
                .where(HITLMetadata.pause_id == pause_id)
                .values(status="escalated")
            )

        await db.flush()
    except Exception as e:
        logger.error(f"Failed to persist support escalation for {session_id}: {e}")

    return Command(
        goto="answer_node",
        update={
            "response": empathetic_message,
            "hitl_triggered": False,
        },
    )
