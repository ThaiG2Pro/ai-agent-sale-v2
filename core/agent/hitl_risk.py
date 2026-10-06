"""Why this exists: the WP-V2-4 risk-score tiers (anti approval-fatigue) were pure
scoring helpers buried in the 670-line hitl_guard_node module.
What it does: order value, customer-history factor, value normalization, composite
risk score, tier resolution with the non-configurable safety invariant, and the
Tier-1 auto-proceed eligibility check.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.config import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ── WP-V2-4 risk-score HITL tiers (anti approval-fatigue) ──────────────────
# risk = W_CONF·(1-confidence) + W_VALUE·order_value_norm + W_HISTORY·history
# Tier 1: auto-proceed. Tier 2: interrupt (pre-V2-4 behavior). Tier 3: straight
# to the support queue. Kill switch RISK_HITL_ENABLED=False restores the old
# binary triggers (ORDER_PLACEMENT OR confidence < threshold).


def _order_value(order_info: dict | None) -> float | None:
    """Total order value in VND, or None when it cannot be determined."""
    if not order_info:
        return None
    price = order_info.get("price")
    if price is None:
        return None
    try:
        quantity = float(order_info.get("quantity") or 1)
        value = float(price) * quantity
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


async def _history_factor(customer_id: str | None, db: AsyncSession | None) -> float:
    """Customer-history risk term in [0,1] from intent_tracking. 1.0 = unknown.

    Conservative defaults: no customer_id, no rows, or a DB error all read as
    a NEW customer (max history risk).
    """
    if not customer_id or db is None:
        return 1.0
    try:
        from sqlalchemy import select as sa_select

        from models.schema import IntentStatus, IntentTracking

        result = await db.execute(
            sa_select(IntentTracking.status).where(IntentTracking.customer_id == customer_id)
        )
        statuses = {str(s) for s in result.scalars().all()}
    except Exception:
        logger.warning("history_factor lookup failed; treating customer as new", exc_info=True)
        return 1.0
    if not statuses:
        return 1.0
    if IntentStatus.CONVERTED in statuses:
        return 0.0  # Has purchased before — lowest history risk
    if statuses & {IntentStatus.ENGAGED, IntentStatus.AWAITING_QUOTE, IntentStatus.CONTACTED}:
        return 0.5  # Known, actively engaged customer
    return 0.8  # Tracked but NEW/LOST only


def _value_norm(intent: str | None, order_value: float | None) -> float:
    """Order-value risk term in [0,1].

    Only ORDER_PLACEMENT has money at stake: a missing/unparseable value there
    reads as MAX risk (conservative); other intents carry no value risk.
    """
    if intent != "ORDER_PLACEMENT":
        return 0.0
    if order_value is None:
        return 1.0  # Conservative: unknown order value is high risk
    return min(order_value / settings.HITL_ORDER_VALUE_NORM_CAP, 1.0)


def _risk_score(confidence: float, value_norm: float, history: float) -> float:
    """Weighted composite risk in [0,1]."""
    conf = min(max(confidence, 0.0), 1.0)
    return (
        settings.HITL_RISK_W_CONF * (1.0 - conf)
        + settings.HITL_RISK_W_VALUE * value_norm
        + settings.HITL_RISK_W_HISTORY * history
    )


def _resolve_risk_tier(intent: str | None, order_value: float | None, risk: float) -> int:
    """Map risk score to tier 1/2/3 and enforce the safety invariant.

    SAFETY INVARIANT (non-configurable): an ORDER_PLACEMENT whose value is
    unknown — or at/above HITL_TIER1_MAX_ORDER_VALUE (T07 default 10tr) — is
    always >= Tier 2. No weight/threshold tuning can auto-approve such an
    order. v3-0 P2 (T07): the pre-P2 hardcode "every ORDER pauses at Tier 2"
    only remains under the ORDER_HITL_V3_ENABLED=False kill switch; further
    Tier-1 conditions (unique product, phone+address, stock) are checked by
    _tier1_eligibility in the node.
    """
    if intent == "ORDER_PLACEMENT" and not settings.ORDER_HITL_V3_ENABLED:
        # Pre-v3-0-P2 behavior: all orders pause at Tier 2 for review.
        return 2

    if risk >= settings.HITL_RISK_TIER3_THRESHOLD:
        tier = 3
    elif risk >= settings.HITL_RISK_TIER1_THRESHOLD:
        tier = 2
    else:
        tier = 1

    if intent == "ORDER_PLACEMENT" and (
        order_value is None or order_value >= settings.HITL_TIER1_MAX_ORDER_VALUE
    ):
        tier = max(tier, 2)
    return tier


async def _tier1_eligibility(order_info: dict | None, db: AsyncSession | None) -> tuple[bool, str]:
    """v3-0 P2 (T07): remaining Tier-1 auto-proceed conditions.

    Value < threshold is already enforced by _resolve_risk_tier; here:
    uniquely determined product, phone + address present, stock sufficient.
    Any missing/unverifiable condition falls back to Tier 2 — never Tier 1.
    """
    if not order_info or not order_info.get("product_id"):
        return False, "no_unique_product"
    if not order_info.get("phone"):
        return False, "missing_phone"
    if not order_info.get("address"):
        return False, "missing_address"
    if db is None:
        return False, "no_db_for_stock_check"
    try:
        from sqlalchemy import select as sa_select

        from models.schema import Product

        qty = int(order_info.get("quantity") or 1)
        stock = (
            await db.execute(
                sa_select(Product.stock_quantity).where(Product.id == order_info["product_id"])
            )
        ).scalar_one_or_none()
        if stock is None or stock < qty:
            return False, "insufficient_stock"
    except Exception:
        logger.warning("Tier1 stock check failed; falling back to Tier 2", exc_info=True)
        return False, "stock_check_error"
    return True, "ok"
