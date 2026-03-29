"""Memory admin API routes (Week 5, US2, T070-T072).

Why this exists: Expose intent tracking for human operators to manage leads.
What it does: GET single intent, LIST intents with filters, UPDATE status.
Admin-only: Requires X-Admin-Key header (FR-008a).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from models.schema import IntentTracking
from services.database import get_db
from services.memory.intent_tracker import (
    CustomerNotFoundError,
    IntentLockConflictError,
    IntentTracker,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


# === Pydantic Schemas ===


class IntentTrackingResponse(BaseModel):
    """Response schema for single intent query."""

    model_config = ConfigDict(from_attributes=True)

    customer_id: str
    budget_range: str | None = None
    urgency_level: str
    product_interest: list[str]
    decision_timeline: str | None = None
    contact_preference: str | None = None
    version: int
    intent_status: str


class UpdateIntentStatusRequest(BaseModel):
    """Request schema for status update."""

    new_status: str
    expected_version: int


class PaginatedIntentList(BaseModel):
    """Paginated list response."""

    items: list[IntentTrackingResponse]
    total: int
    limit: int
    offset: int


# === Dependencies ===


def require_admin_key(
    x_admin_key: Annotated[str | None, Query()] = None,
    x_admin_key_header: Annotated[str | None, Header()] = None,
) -> bool:
    """Require valid admin key for sensitive endpoints.

    Query param: x_admin_key (for testing)
    Header: X-Admin-Key (for production)
    """
    from core.config import settings

    # Try header first, then query parameter
    key = x_admin_key_header or x_admin_key
    if not key:
        raise HTTPException(status_code=401, detail="Admin key required")
    if key != settings.X_ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Invalid admin key")
    return True


# === Endpoints ===


@router.get("/intent/{customer_id}", response_model=IntentTrackingResponse)
async def get_intent(
    customer_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> IntentTrackingResponse:
    """Get current intent state for a customer (public endpoint).

    T070: GET /memory/intent/{customer_id}

    Args:
        customer_id: Unique customer identifier
        db: Database session

    Returns:
        Current intent tracking record

    Raises:
        404: Customer not found
    """
    from sqlalchemy import select

    stmt = select(IntentTracking).where(IntentTracking.customer_id == customer_id)
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"Customer {customer_id} not found",
        )

    return IntentTrackingResponse.from_orm(row)


@router.get("/intents", response_model=PaginatedIntentList)
async def list_intents(
    urgency_level: Annotated[str | None, Query()] = None,
    intent_status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
    offset: Annotated[int, Query(ge=0)] = 0,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
    _admin: Annotated[bool, Depends(require_admin_key)] = True,
) -> PaginatedIntentList:
    """List intents with optional filtering (admin-only).

    T071: GET /memory/intents?urgency_level=HIGH&intent_status=NEW

    Query parameters:
        urgency_level: Filter by urgency (HIGH, MEDIUM, LOW, UNKNOWN)
        intent_status: Filter by status (NEW, ENGAGED, AWAITING_QUOTE, etc.)
        limit: Items per page (default 10, max 50)
        offset: Pagination offset (default 0)

    Returns:
        Paginated list of intent records ordered by urgency DESC, updated_at DESC

    Raises:
        401: Unauthorized (missing/invalid admin key)
        400: Invalid filter values
    """
    from sqlalchemy import and_, func, select

    # Build filter conditions
    conditions = []
    if urgency_level:
        conditions.append(IntentTracking.urgency_level == urgency_level)
    if intent_status:
        conditions.append(IntentTracking.intent_status == intent_status)

    # Count total with filters
    count_stmt = select(func.count(IntentTracking.id))
    if conditions:
        count_stmt = count_stmt.where(and_(*conditions))
    count_result = await db.execute(count_stmt)
    total = count_result.scalar_one()

    # Fetch paginated results ordered by urgency DESC, updated_at DESC
    stmt = select(IntentTracking)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = (
        stmt.order_by(
            IntentTracking.urgency_level.desc(),
            IntentTracking.updated_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )

    result = await db.execute(stmt)
    rows = result.scalars().all()

    return PaginatedIntentList(
        items=[IntentTrackingResponse.from_orm(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.patch("/intent/{customer_id}/status", response_model=IntentTrackingResponse)
async def update_intent_status(
    customer_id: str,
    body: UpdateIntentStatusRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin: Annotated[bool, Depends(require_admin_key)] = True,
) -> IntentTrackingResponse:
    """Update intent status with optimistic lock (admin-only).

    T072: PATCH /memory/intent/{customer_id}/status

    Request body:
        new_status: New status value (e.g., "CONTACTED", "CONVERTED")
        expected_version: Expected current version (optimistic lock)

    Returns:
        Updated intent tracking record

    Raises:
        400: Invalid request body
        401: Unauthorized (missing/invalid admin key)
        404: Customer not found
        409: Version conflict (stale expected_version)
    """
    tracker = IntentTracker()

    try:
        updated_row = await tracker.update_status(
            customer_id=customer_id,
            new_status=body.new_status,
            expected_version=body.expected_version,
            trigger="admin",
            db=db,
        )
        return IntentTrackingResponse.from_orm(updated_row)

    except CustomerNotFoundError as err:
        raise HTTPException(
            status_code=404,
            detail=f"Customer {customer_id} not found",
        ) from err
    except IntentLockConflictError as err:
        raise HTTPException(
            status_code=409,
            detail="Version conflict: stale expected_version",
        ) from err
