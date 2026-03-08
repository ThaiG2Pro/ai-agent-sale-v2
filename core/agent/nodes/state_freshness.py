"""state_freshness_validator_node — checks inventory and prices before order execution."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from langgraph.types import Command
from sqlalchemy import select

from core.agent.state import AgentState, HITLReasonEnum
from core.config import settings
from models.schema import Product

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def state_freshness_validator_node(state: AgentState, config: RunnableConfig) -> Command:
    """Re-verifies inventory and price against DB before executing order (Phase 10)."""
    db = cast("AsyncSession", config["configurable"].get("db"))
    order_info = state.get("order_info")

    if not order_info or "product_id" not in order_info:
        # Fallthrough if not an order flow or missing info
        return Command(goto="answer_node")

    product_id = order_info["product_id"]

    # --- T035: Stock check ---
    stmt = select(Product).where(Product.id == product_id)
    result = await db.execute(stmt)
    product = result.scalar_one_or_none()

    if not product:
        logger.warning(f"Product {product_id} not found during freshness check.")
        return Command(
            goto="customer_support_node", update={"hitl_rejection_reason": "product_not_found"}
        )

    if product.stock_quantity <= 0:
        return Command(
            goto="customer_support_node", update={"hitl_rejection_reason": "out_of_stock"}
        )

    # --- T036: Price delta check ---
    # Need to get the price the customer/admin originally agreed upon.
    # We look for "approved_price" or "price" in order_info.
    approved_price = float(order_info.get("approved_price", order_info.get("price", 0.0)))
    current_price = float(product.price)

    if approved_price > 0:
        delta = abs(current_price - approved_price) / approved_price

        if delta >= settings.HITL_PRICE_DELTA_THRESHOLD:
            # Update order_info so admin sees the new price
            updated_order_info = {**order_info, "approved_price": current_price}

            return Command(
                goto="hitl_guard_node",
                update={
                    "order_info": updated_order_info,
                    "hitl_triggered": False,
                    "hitl_pause_id": None,
                    "hitl_reason": HITLReasonEnum.STALE_PRICE,
                },
            )

    # --- T037: Freshness OK path ---
    return Command(goto="order_execution_node", update={"hitl_freshness_valid": True})
