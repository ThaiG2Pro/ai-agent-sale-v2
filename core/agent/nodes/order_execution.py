"""order_execution_node — finalizes order placement.

Why: Core revenue-generating node. Deducts stock and records persistent order.
What: Atomic transaction to decrement stock and insert order record.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from langgraph.types import Command
from sqlalchemy import insert, update

from core.tools.timeout_guard import wrap_tool_with_timeout
from models.schema import Order, Product

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.agent.state import AgentState

logger = logging.getLogger(__name__)


async def order_execution_node(state: AgentState, config: RunnableConfig) -> Command:
    """Performs atomic order execution (Phase 11).

    1. Decrement stock_quantity in 'products' table.
    2. Insert record into 'orders' table.
    3. Return confirmation response.
    """
    db = cast("AsyncSession", config["configurable"].get("db"))
    session_id = state["session_id"]
    order_info = state.get("order_info")

    if not order_info or "product_id" not in order_info:
        logger.error(f"Missing order_info for session {session_id}")
        return Command(goto="answer_node", update={"error": "Missing order information"})

    from services.draft_orders import items_total, normalize_items

    # v3-0 P2 (T05): confirm reads items[] — multi-item aware (O14 root fix).
    items = normalize_items(order_info)
    quantity = int(order_info.get("quantity", 1))
    customer_id = state.get("customer_id", "anonymous")  # fallback if missing

    try:
        # T038: Atomic transaction
        # 1. Decrement stock per item with guard (prevents negative stock).
        # WHERE stock >= qty is the race-condition guard; on a mid-list
        # failure the already-decremented items are compensated back so the
        # session holds no partial decrement.
        decremented: list[tuple[str, int]] = []
        for item in items:
            item_pid = item.get("product_id")
            item_qty = int(item.get("quantity") or 1)
            stock_stmt = (
                update(Product)
                .where(Product.id == item_pid, Product.stock_quantity >= item_qty)
                .values(stock_quantity=Product.stock_quantity - item_qty)
            )
            stock_result_wrapper = await wrap_tool_with_timeout(
                db.execute(stock_stmt),
                tool_name="order_processing",
            )
            if not stock_result_wrapper.success:
                return Command(
                    goto="answer_node",
                    update={"error": stock_result_wrapper.error or "Order processing timed out"},
                )
            if stock_result_wrapper.data.rowcount == 0:
                # Stock was insufficient or product missing — compensate.
                logger.warning(f"Stock exhaustion or race for product {item_pid}")
                for done_pid, done_qty in decremented:
                    await db.execute(
                        update(Product)
                        .where(Product.id == done_pid)
                        .values(stock_quantity=Product.stock_quantity + done_qty)
                    )
                return Command(
                    goto="customer_support_node",
                    update={"hitl_rejection_reason": "out_of_stock_last_minute"},
                )
            decremented.append((item_pid, item_qty))

        # 2. Record the confirmed order. v3-0 P2 (T05): when the pause created
        # a draft row, CONFIRM transitions that row (status → confirmed) instead
        # of inserting a parallel record; an expired draft (TTL 24h) is never
        # confirmed — the agent re-quotes instead.
        import uuid

        from core.config import settings as _settings
        from services.draft_orders import confirm_draft

        draft_id = order_info.get("draft_order_id") if _settings.ORDER_HITL_V3_ENABLED else None

        order_uuid = uuid.UUID(str(draft_id)) if draft_id else uuid.uuid4()
        order_code = f"ORD-{str(order_uuid)[:8].upper()}"
        order_info = {**order_info, "items": items, "order_id": order_code, "status": "confirmed"}

        if draft_id:
            confirmed = await confirm_draft(db, draft_id, order_info)
            if not confirmed:
                # Draft expired or superseded meanwhile — compensate stock and
                # re-quote instead of confirming stale terms (T05 rule 3).
                for done_pid, done_qty in decremented:
                    await db.execute(
                        update(Product)
                        .where(Product.id == done_pid)
                        .values(stock_quantity=Product.stock_quantity + done_qty)
                    )
                logger.warning("Draft %s expired/inactive at confirm time", draft_id)
                return Command(
                    goto="answer_node",
                    update={
                        "response": (
                            "Dạ, báo giá cũ của đơn này đã hết hiệu lực (quá 24 giờ). "
                            "Em xin phép báo giá lại theo giá hiện tại — anh/chị xác nhận "
                            "lại giúp em trước khi lên đơn nhé ạ!"
                        ),
                        "order_info": {**order_info, "status": "expired"},
                    },
                )
        else:
            order_stmt = insert(Order).values(
                id=order_uuid,
                session_id=session_id,
                customer_id=customer_id,
                order_info=order_info,
                status="confirmed",
            )
            order_insert_wrapper = await wrap_tool_with_timeout(
                db.execute(order_stmt),
                tool_name="order_processing",
            )
            if not order_insert_wrapper.success:
                return Command(
                    goto="answer_node",
                    update={"error": order_insert_wrapper.error or "Order processing timed out"},
                )

        # Flush to DB (the graph caller or checkpointer might handle commit,
        # but for business data we should be explicit if we are not sharing tx)
        # Spec says "within a single DB transaction".
        flush_wrapper = await wrap_tool_with_timeout(
            db.flush(),
            tool_name="order_processing",
        )
        if not flush_wrapper.success:
            return Command(
                goto="answer_node",
                update={"error": flush_wrapper.error or "Order processing timed out"},
            )

        # v3-0 P4 (T11 4.3 mandatory condition): stock just changed — cached
        # availability/pricing answers are now stale. Best-effort: an
        # invalidation failure never blocks the order.
        if _settings.PRECLASSIFY_WHITELIST_ENABLED:
            try:
                from services.semantic_cache import invalidate_cache

                await invalidate_cache(db)
            except Exception:
                logger.warning("semantic cache invalidation after stock change failed")

        # 3. Compose confirmation message (SC5: append pending INFO answers if any)
        total_price = items_total(order_info)
        price_str = f"{total_price:,.0f} đ".replace(",", ".") if total_price > 0 else ""
        price_suffix = f" (Tổng tiền: {price_str})" if price_str else ""

        if len(items) > 1:
            # v3-0 P2 (O14): multi-item order — list every line item.
            lines = "\n".join(
                f"• {i.get('product_name') or i.get('sku') or 'sản phẩm'} x "
                f"{int(i.get('quantity') or 1)}"
                for i in items
            )
            order_desc = f"Đơn hàng gồm:\n{lines}\n{price_suffix.strip()} "
        else:
            order_desc = (
                f"Đơn hàng **{order_info.get('name', 'sản phẩm')}** "
                f"(Số lượng: {quantity}){price_suffix} "
            )

        confirmation_msg = (
            f"🎉 **Đặt hàng thành công!**\n"
            f"{order_desc}"
            f"đã được hệ thống xác nhận thành công.\n"
            f"Mã đơn hàng: `{order_code}`.\n"
            f"Cảm ơn quý khách đã ủng hộ shop!"
        )

        # v3-0 P2 (O27/2.7): the admin's approve note reaches the customer.
        admin_note = state.get("hitl_admin_reason")
        if admin_note:
            confirmation_msg += f"\n\n💬 Ghi chú từ shop: {admin_note}"

        # SC5-fix: if customer asked INFO questions while waiting, answer them now.
        pending_questions = state.get("pending_info_questions")
        if pending_questions:
            from services.ai import AIGateway

            try:
                citations = state.get("citations") or []
                context = "\n".join(
                    c.get("name", "") + ": " + c.get("description", "")
                    for c in citations
                    if isinstance(c, dict)
                )
                qa_resp = await AIGateway.complete(
                    model="light-chat",
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "You are a helpful sales assistant. "
                                "The customer just had their order confirmed. "
                                "They also asked some questions while waiting. "
                                "Answer them briefly and naturally in Vietnamese. "
                                f"Product context: {context or order_info.get('name', '')}"
                            ),
                        },
                        {"role": "user", "content": pending_questions},
                    ],
                )
                qa_answer = qa_resp.choices[0].message.content or ""
                confirmation_msg += f"\n\nNgoài ra, trả lời câu hỏi của bạn: {qa_answer.strip()}"
                logger.info("SC5: appended INFO answer to order confirmation")
            except Exception as qa_exc:
                logger.warning("SC5: failed to fetch INFO answer: %s", qa_exc)

        return Command(
            goto="answer_node",
            update={
                "response": confirmation_msg,
                "order_info": {**order_info, "status": "confirmed"},
                "pending_info_questions": None,
                "hitl_admin_reason": None,
            },
        )

    except Exception as e:
        logger.exception(f"Order execution failed for session {session_id}")
        return Command(goto="answer_node", update={"error": f"Order execution failed: {e!s}"})
