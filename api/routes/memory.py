"""Memory admin API routes (Week 5, US2, T070-T072).

Why this exists: Expose intent tracking for human operators to manage leads.
What it does: GET single intent, LIST intents with filters, UPDATE status.
Admin-only: Requires X-Admin-Key header (FR-008a).
Phase 7d (T138-T139): Semantic memory endpoint for cross-session retrieval.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import (
    AsyncSession,  # noqa: TC002 - NEEDED: for Pydantic schema resolution
)
from sqlalchemy.orm import aliased

from models.schema import IntentTracking, SalesIntentLog
from services.database import get_db
from services.memory.intent_tracker import (
    CustomerNotFoundError,
    IntentLockConflictError,
    IntentTracker,
)

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


class SemanticMemoryResult(BaseModel):
    """Single semantic memory result (T139)."""

    summary_id: str
    summary_text: str
    thread_id: str
    similarity_score: float = Field(..., ge=0.0, le=1.0)


class SemanticMemoryResponse(BaseModel):
    """Response schema for semantic memory endpoint (T139).

    Includes active/stale counts and top-K results for dashboard.
    """

    customer_id: str
    active_count: int = Field(..., ge=0, description="Number of active embeddings")
    stale_count: int = Field(..., ge=0, description="Number of stale embeddings")
    results: list[SemanticMemoryResult] = Field(
        default_factory=list, description="Top-K semantic memory results"
    )


# === Helpers ===
#
# IntentTracking only stores lightweight state (status/version, optimistic
# lock). The actual extracted signal detail (urgency, budget, product
# interest...) lives in SalesIntentLog, written once per turn by
# services.memory.background._maybe_extract_intent. The public response
# combines both: state from IntentTracking, latest extracted signal from
# SalesIntentLog (a customer may have no log yet — extraction is skipped for
# low-signal turns, so all detail fields degrade to safe defaults).


def _latest_sales_intent_log_join(stmt, latest_log: type[SalesIntentLog]):
    """Outer-join `stmt` (must select FROM IntentTracking) with each row's
    most recent SalesIntentLog, via a correlated subquery."""
    latest_log_id = (
        select(SalesIntentLog.id)
        .where(SalesIntentLog.customer_id == IntentTracking.customer_id)
        .order_by(SalesIntentLog.created_at.desc())
        .limit(1)
        .correlate(IntentTracking)
        .scalar_subquery()
    )
    return stmt.outerjoin(latest_log, latest_log.id == latest_log_id)


def _build_intent_response(
    intent: IntentTracking, log: SalesIntentLog | None
) -> IntentTrackingResponse:
    """Combine IntentTracking (state/version) with the latest SalesIntentLog
    (extracted signal detail, if any) into the public response shape."""
    return IntentTrackingResponse(
        customer_id=intent.customer_id,
        budget_range=log.budget_range if log else None,
        urgency_level=(log.urgency_level if log and log.urgency_level else "UNKNOWN"),
        product_interest=list(log.product_interest) if log and log.product_interest else [],
        decision_timeline=log.decision_timeline if log else None,
        contact_preference=log.contact_preference if log else None,
        version=intent.version,
        intent_status=intent.status,
    )


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
    latest_log = aliased(SalesIntentLog)
    stmt = (
        _latest_sales_intent_log_join(
            select(IntentTracking, latest_log).where(IntentTracking.customer_id == customer_id),
            latest_log,
        )
        .order_by(IntentTracking.updated_at.desc())
        .limit(1)
    )

    result = await db.execute(stmt)
    row = result.first()

    if not row:
        raise HTTPException(
            status_code=404,
            detail=f"Customer {customer_id} not found",
        )

    intent, log = row
    return _build_intent_response(intent, log)


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
    latest_log = aliased(SalesIntentLog)

    # Build filter conditions. urgency_level lives on SalesIntentLog (the
    # extracted signal detail), intent_status maps to IntentTracking.status.
    conditions = []
    if urgency_level:
        conditions.append(latest_log.urgency_level == urgency_level)
    if intent_status:
        conditions.append(IntentTracking.status == intent_status)

    # Count total with filters
    count_stmt = _latest_sales_intent_log_join(
        select(func.count(IntentTracking.id)), latest_log
    )
    if conditions:
        count_stmt = count_stmt.where(and_(*conditions))
    count_result = await db.execute(count_stmt)
    total = count_result.scalar_one()

    # Fetch paginated results ordered by urgency DESC, updated_at DESC
    stmt = _latest_sales_intent_log_join(select(IntentTracking, latest_log), latest_log)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    stmt = (
        stmt.order_by(
            latest_log.urgency_level.desc(),
            IntentTracking.updated_at.desc(),
        )
        .limit(limit)
        .offset(offset)
    )

    result = await db.execute(stmt)
    rows = result.all()

    return PaginatedIntentList(
        items=[_build_intent_response(intent, log) for intent, log in rows],
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

        log_stmt = (
            select(SalesIntentLog)
            .where(SalesIntentLog.customer_id == customer_id)
            .order_by(SalesIntentLog.created_at.desc())
            .limit(1)
        )
        log_row = (await db.execute(log_stmt)).scalar_one_or_none()

        return _build_intent_response(updated_row, log_row)

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


@router.get("/semantic/{customer_id}", response_model=SemanticMemoryResponse)
async def get_semantic_memory(
    customer_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> SemanticMemoryResponse:
    """Get semantic memory for customer (T138-T139).

    Retrieve active semantic memory (summaries) and embeddings for a customer.
    Includes counts of active/stale embeddings.

    Functional Requirements:
    - FR-009: Retrieve semantic memory for context injection
    - FR-008b: Strict customer_id isolation (no cross-customer leakage)

    Args:
        customer_id: Customer identifier (must start with "cust_")
        db: Async database session

    Returns:
        Semantic memory response with active/stale counts and top results

    Raises:
        HTTPException: 400 if customer_id invalid, 500 if DB error
    """
    import logging

    logger = logging.getLogger(__name__)

    try:
        if not customer_id or not customer_id.startswith("cust_"):
            raise HTTPException(status_code=400, detail="Invalid customer_id format")

        from models.schema import SemanticMemory
        from services.memory.semantic_memory import SemanticMemoryService

        service = SemanticMemoryService()

        # Retrieve semantic memory for customer (T123)
        results = await service.retrieve(
            customer_id=customer_id,
            query="",  # Empty query to get all active embeddings
            db=db,
            top_k=10,  # Return top 10 for dashboard visibility
            min_score=0.0,  # No threshold for listing
        )

        # T139: Count active/stale embeddings
        stmt_active = select(func.count(SemanticMemory.id)).where(
            SemanticMemory.customer_id == customer_id, SemanticMemory.status == "ACTIVE"
        )
        result_active = await db.execute(stmt_active)
        active_count = result_active.scalar() or 0

        # Count stale
        stmt_stale = select(func.count(SemanticMemory.id)).where(
            SemanticMemory.customer_id == customer_id, SemanticMemory.status == "STALE"
        )
        result_stale = await db.execute(stmt_stale)
        stale_count = result_stale.scalar() or 0

        # Map results to response schema
        response_results = [
            SemanticMemoryResult(
                summary_id=r.summary_id,
                summary_text=r.summary_text,
                thread_id=r.session_id,
                similarity_score=r.similarity_score,
            )
            for r in results
        ]

        logger.debug(
            "Semantic memory retrieved",
            extra={
                "customer_id": customer_id,
                "active_count": active_count,
                "stale_count": stale_count,
                "results_count": len(response_results),
            },
        )

        return SemanticMemoryResponse(
            customer_id=customer_id,
            active_count=active_count,
            stale_count=stale_count,
            results=response_results,
        )

    except HTTPException:
        raise
    except Exception as e:
        import logging

        logger = logging.getLogger(__name__)
        logger.error(
            "Semantic memory retrieval failed",
            extra={
                "customer_id": customer_id,
                "error": str(e),
            },
        )
        raise HTTPException(status_code=500, detail="Semantic memory retrieval failed") from e


@router.delete("/customer/{customer_id}")
async def delete_customer_memory(
    customer_id: str,
    confirm: Annotated[bool, Query()] = False,
    db: Annotated[AsyncSession, Depends(get_db)] = None,
    _admin: Annotated[bool, Depends(require_admin_key)] = True,
) -> dict:
    """Delete all customer memory (RTBF - Right to be Forgotten) (T144-T145).

    Cascade delete: IntentTracking → ConversationSummaries → SemanticMemory.
    Requires confirm=true to proceed (safety check for accidental deletes).
    Audit trail via structured logging.

    Functional Requirements:
    - FR-019: Right to be forgotten / data deletion
    - FR-001b: Audit trail of deletions (via observability/logging)

    Args:
        customer_id: Customer identifier
        confirm: Safety confirmation flag (must be true)
        db: Async database session
        _admin: Admin authorization check

    Returns:
        Deletion report with counts of deleted records

    Raises:
        HTTPException: 400 if confirm=false, 401 if unauthorized, 500 if DB error
    """
    import logging
    from datetime import datetime

    from models.schema import ConversationSummary, IntentTracking, SemanticMemory

    logger = logging.getLogger(__name__)

    if not confirm:
        raise HTTPException(
            status_code=400, detail="confirm=true required to delete customer memory (RTBF)"
        )

    if not customer_id or not customer_id.startswith("cust_"):
        raise HTTPException(status_code=400, detail="Invalid customer_id format")

    try:
        # T145: Transactional cascade delete
        deleted_counts = {
            "intent_tracking": 0,
            "conversation_summaries": 0,
            "semantic_memory": 0,
        }

        # Delete IntentTracking
        stmt_intent = select(func.count(IntentTracking.id)).where(
            IntentTracking.customer_id == customer_id
        )
        result = await db.execute(stmt_intent)
        deleted_counts["intent_tracking"] = result.scalar() or 0

        stmt = select(IntentTracking).where(IntentTracking.customer_id == customer_id)
        rows = (await db.execute(stmt)).scalars().all()
        for row in rows:
            await db.delete(row)

        # Delete ConversationSummaries
        stmt_summary = select(func.count(ConversationSummary.id)).where(
            ConversationSummary.customer_id == customer_id
        )
        result = await db.execute(stmt_summary)
        deleted_counts["conversation_summaries"] = result.scalar() or 0

        stmt = select(ConversationSummary).where(ConversationSummary.customer_id == customer_id)
        rows = (await db.execute(stmt)).scalars().all()
        for row in rows:
            await db.delete(row)

        # Delete SemanticMemory
        stmt_semantic = select(func.count(SemanticMemory.id)).where(
            SemanticMemory.customer_id == customer_id
        )
        result = await db.execute(stmt_semantic)
        deleted_counts["semantic_memory"] = result.scalar() or 0

        stmt = select(SemanticMemory).where(SemanticMemory.customer_id == customer_id)
        rows = (await db.execute(stmt)).scalars().all()
        for row in rows:
            await db.delete(row)

        # Commit transaction
        await db.commit()

        logger.info(
            "Customer memory deleted (RTBF)",
            extra={
                "customer_id": customer_id,
                "deleted_counts": deleted_counts,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )

        return {
            "customer_id": customer_id,
            "deleted": deleted_counts,
            "status": "success",
        }

    except Exception as e:
        await db.rollback()
        logger.error(
            "Customer memory deletion failed",
            extra={
                "customer_id": customer_id,
                "error": str(e),
            },
        )
        raise HTTPException(status_code=500, detail="Customer memory deletion failed") from e
