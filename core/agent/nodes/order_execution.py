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

    product_id = order_info["product_id"]
    quantity = int(order_info.get("quantity", 1))
    customer_id = state.get("customer_id", "anonymous")  # fallback if missing

    try:
        # T038: Atomic transaction
        # 1. Decrement stock with guard (prevents negative stock)
        # We rely on the DB session provided in config.
        # It's better to use a single transaction.

        # We use a WHERE clause to ensure stock >= quantity (Race condition guard)
        stock_stmt = (
            update(Product)
            .where(Product.id == product_id, Product.stock_quantity >= quantity)
            .values(stock_quantity=Product.stock_quantity - quantity)
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
        stock_result = stock_result_wrapper.data

        if stock_result.rowcount == 0:
            # Stock was insufficient or product missing
            logger.warning(f"Stock exhaustion or race for product {product_id}")
            return Command(
                goto="customer_support_node",
                update={"hitl_rejection_reason": "out_of_stock_last_minute"},
            )

        # 2. Insert order record
        order_stmt = insert(Order).values(
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

        # 3. Compose confirmation message (SC5: append pending INFO answers if any)
        confirmation_msg = (
            f"Great news! Your order for {order_info.get('name', 'the product')} "
            f"(Quantity: {quantity}) has been successfully placed. "
            f"Order Reference: {session_id}"
        )

        # SC5-fix: if customer asked INFO questions while waiting, answer them now.
        pending_questions = state.get("pending_info_questions")
        if pending_questions:
            import litellm

            from core.config import settings

            try:
                citations = state.get("citations") or []
                context = "\n".join(
                    c.get("name", "") + ": " + c.get("description", "")
                    for c in citations
                    if isinstance(c, dict)
                )
                qa_resp = await litellm.acompletion(
                    model=settings.LIGHT_CHAT_MODEL,
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
            },
        )

    except Exception as e:
        logger.exception(f"Order execution failed for session {session_id}")
        return Command(goto="answer_node", update={"error": f"Order execution failed: {e!s}"})
