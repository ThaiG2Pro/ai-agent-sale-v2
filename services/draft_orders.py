"""
Why this exists: v3-0 P2 (T05) — drafts used to live only inside checkpointed
agent state, so an ADD-ON replaced the original order (O14) and nothing
auditable survived a change of mind. A draft is now a row in the `orders`
table with a status lifecycle; the agent never edits a draft, it creates a
new one that supersedes the old one in the same transaction.
What it does: draft creation with a soft cap per customer (supersede-oldest),
inline supersede chaining via orders.supersedes_id, lazy TTL expiry on read
(no background job, rows are never deleted), items[] normalization for
multi-item orders, and draft confirmation/state transitions.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select, update
from uuid_utils import uuid7

from core.config import settings
from models.schema import Order

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Statuses that count against the per-customer active-draft cap.
ACTIVE_DRAFT_STATUSES: tuple[str, ...] = ("draft", "pending_review")


def draft_expiry_cutoff(now: datetime | None = None) -> datetime:
    """Oldest created_at still considered fresh (T05: TTL 24h, lazy on read)."""
    now = now or datetime.now(UTC)
    return now - timedelta(hours=settings.DRAFT_ORDER_TTL_HOURS)


def is_draft_expired(order: Order, now: datetime | None = None) -> bool:
    """Lazy expiry check — the row itself is never mutated on expiry."""
    created = order.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return created < draft_expiry_cutoff(now)


def normalize_items(order_info: dict[str, Any]) -> list[dict[str, Any]]:
    """Return order_info['items'] — synthesized from flat fields when absent.

    Pre-P2 order_info carried a single flat product (product_id/name/price/
    quantity). items[] is the canonical multi-item shape (T05, root fix for
    O14 ADD-ON and the path to combos O12).
    """
    items = order_info.get("items")
    if isinstance(items, list) and items:
        return items
    if not order_info.get("product_id"):
        return []
    return [
        {
            "product_id": str(order_info["product_id"]),
            "product_name": order_info.get("name") or order_info.get("product_name") or "",
            "sku": order_info.get("sku", ""),
            "quantity": int(order_info.get("quantity") or 1),
            "unit_price": float(order_info.get("price") or 0.0),
        }
    ]


def items_total(order_info: dict[str, Any]) -> float:
    """Total order value in VND across items[] (approved_price overrides).

    approved_price is the admin/legacy single-product unit override — when
    present it keeps its pre-P2 meaning (unit price x quantity).
    """
    approved = order_info.get("approved_price")
    if approved is not None:
        try:
            return float(approved) * float(order_info.get("quantity") or 1)
        except (TypeError, ValueError):
            return 0.0
    total = 0.0
    for item in normalize_items(order_info):
        try:
            total += float(item.get("unit_price") or 0.0) * float(item.get("quantity") or 1)
        except (TypeError, ValueError):
            continue
    return total


async def get_active_drafts(db: AsyncSession, customer_id: str) -> list[Order]:
    """Active (non-expired) drafts for a customer, oldest first.

    Expiry is a lazy read-side filter on created_at — expired rows simply
    stop matching; they are not updated (T05 rule 4).
    """
    stmt = (
        select(Order)
        .where(
            Order.customer_id == customer_id,
            Order.status.in_(ACTIVE_DRAFT_STATUSES),
            Order.created_at >= draft_expiry_cutoff(),
        )
        .order_by(Order.created_at.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def create_draft(
    db: AsyncSession,
    *,
    session_id: str,
    customer_id: str,
    order_info: dict[str, Any],
    status: str = "pending_review",
    supersedes_id: str | UUID | None = None,
) -> Order:
    """Insert a new draft row, superseding inline in the same transaction.

    - If supersedes_id is given, that draft flips to "superseded".
    - Soft cap (T05: 3 active/customer): creating one beyond the cap flips
      the OLDEST active draft(s) to "superseded".
    The agent never edits an existing draft — replacement only.
    """
    supersede_uuid: UUID | None = UUID(str(supersedes_id)) if supersedes_id else None
    if supersede_uuid is not None:
        await db.execute(
            update(Order)
            .where(Order.id == supersede_uuid, Order.status.in_(ACTIVE_DRAFT_STATUSES))
            .values(status="superseded")
        )

    active = await get_active_drafts(db, customer_id)
    active = [o for o in active if o.id != supersede_uuid]
    overflow = len(active) - (settings.DRAFT_ORDER_CAP_PER_CUSTOMER - 1)
    if overflow > 0:
        oldest_ids = [o.id for o in active[:overflow]]
        await db.execute(update(Order).where(Order.id.in_(oldest_ids)).values(status="superseded"))
        logger.info(
            "draft_orders: soft cap hit for customer %s — superseded %d oldest draft(s)",
            customer_id,
            overflow,
        )

    draft_id = uuid7()
    normalized_info = {
        **order_info,
        "items": normalize_items(order_info),
        "draft_order_id": str(draft_id),
    }
    draft = Order(
        id=draft_id,
        session_id=session_id,
        customer_id=customer_id,
        order_info=normalized_info,
        status=status,
        supersedes_id=supersede_uuid,
    )
    db.add(draft)
    await db.flush()
    logger.info(
        "draft_orders: created draft %s (customer=%s, supersedes=%s, items=%d)",
        draft_id,
        customer_id,
        supersede_uuid,
        len(normalized_info["items"]),
    )
    return draft


async def confirm_draft(
    db: AsyncSession, draft_id: str | UUID, order_info: dict[str, Any]
) -> bool:
    """Transition a draft row to confirmed (order_execution path).

    Returns False when the row is missing or already expired (TTL) — the
    caller must re-quote instead of confirming a stale draft (T05 rule 3;
    state_freshness still re-validates price/stock upstream).
    """
    row = (
        await db.execute(select(Order).where(Order.id == UUID(str(draft_id))))
    ).scalar_one_or_none()
    if row is None or row.status not in ACTIVE_DRAFT_STATUSES or is_draft_expired(row):
        return False
    await db.execute(
        update(Order).where(Order.id == row.id).values(status="confirmed", order_info=order_info)
    )
    return True
