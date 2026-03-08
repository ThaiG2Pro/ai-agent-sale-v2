"""order_execution_node — finalizes order placement.

Why: Core revenue-generating node. Deducts stock and records persistent order.
What: Atomic transaction to decrement stock and insert order record.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from langgraph.types import Command
from sqlalchemy import insert, update

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
        stock_result = await db.execute(stock_stmt)

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
        await db.execute(order_stmt)

        # Flush to DB (the graph caller or checkpointer might handle commit,
        # but for business data we should be explicit if we are not sharing tx)
        # Spec says "within a single DB transaction".
        await db.flush()

        # 3. Compose confirmation message
        confirmation_msg = (
            f"Great news! Your order for {order_info.get('name', 'the product')} "
            f"(Quantity: {quantity}) has been successfully placed. "
            f"Order Reference: {session_id}"
        )

        return Command(
            goto="answer_node",
            update={
                "response": confirmation_msg,
                "order_info": {**order_info, "status": "confirmed"},
            },
        )

    except Exception as e:
        logger.exception(f"Order execution failed for session {session_id}")
        return Command(goto="answer_node", update={"error": f"Order execution failed: {e!s}"})
