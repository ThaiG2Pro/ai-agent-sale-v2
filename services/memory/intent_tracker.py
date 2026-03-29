"""Intent Tracking Service with Optimistic Locking (Week 5, FR-015b).

Why this exists: Safely persist and update sales intent under concurrent load.
What it does: INSERT ON CONFLICT DO UPDATE with version field + retry loop.
Pattern: Optimistic locking prevents lost updates without pessimistic locks.

Race condition mitigation (R4): Retry loop up to 3 times with backoff.
Safety (Article VIII): No lost updates under concurrent load.
Non-blocking (Article V): Async-first, no pessimistic locks.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from core.config import settings
from models.schema import IntentTracking

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.agent.state import SalesIntentExtraction

logger = logging.getLogger(__name__)


class IntentLockConflictError(Exception):
    """Raised when optimistic lock conflict not resolved after max retries."""

    pass


class CustomerNotFoundError(Exception):
    """Raised when customer not found in IntentTracking table."""

    pass


class IntentTracker:
    """Manage intent tracking with optimistic locking (FR-015b, SC-010).

    Pattern: Version field + INSERT ON CONFLICT DO UPDATE
    Retry: Up to 3 attempts with exponential backoff [50, 100, 200]ms
    Rationale: Handles concurrent upserts without race conditions
    Safety: Prevents lost updates (R4 mitigation)

    Usage:
        tracker = IntentTracker()
        intent = await tracker.upsert_with_lock(customer_id, session_id, extraction, db)
        updated = await tracker.update_status(customer_id, new_status, expected_version, db)
    """

    async def upsert_with_lock(
        self,
        customer_id: str,
        session_id: str,
        extraction: SalesIntentExtraction,
        db: AsyncSession,
    ) -> IntentTracking:
        """INSERT or UPDATE intent with optimistic lock retry loop.

        Uses INSERT ON CONFLICT DO UPDATE pattern:
        - If customer_id exists: increment version, update fields
        - If customer_id missing: insert new row with version=1

        Retry logic:
        - On version conflict (rowcount=0): retry up to 3 times
        - Backoff delays: [50, 100, 200]ms per settings
        - After max retries: raise IntentLockConflictError

        Args:
            customer_id: Unique customer identifier
            session_id: LangGraph thread_id for context
            extraction: SalesIntentExtraction model with fields to upsert
            db: AsyncSession for database operations

        Returns:
            Updated IntentTracking row

        Raises:
            IntentLockConflictError: If version conflict not resolved after retries
        """
        return await self._do_upsert_with_retry(customer_id, session_id, extraction, db)

    async def _do_upsert_with_retry(
        self,
        customer_id: str,
        session_id: str,
        extraction: SalesIntentExtraction,
        db: AsyncSession,
    ) -> IntentTracking:
        """Execute upsert with retry loop on version conflict."""
        max_retries = settings.INTENT_LOCK_MAX_RETRIES
        backoff_delays = settings.INTENT_LOCK_RETRY_BACKOFF_MS

        for attempt in range(max_retries):
            try:
                # INSERT ... ON CONFLICT DO UPDATE pattern (PostgreSQL-specific)
                stmt = (
                    insert(IntentTracking)
                    .values(
                        customer_id=customer_id,
                        session_id=session_id,
                        budget_range=extraction.budget_range,
                        urgency_level=extraction.urgency_level.value
                        if extraction.urgency_level
                        else "UNKNOWN",
                        product_interest=extraction.product_interest,
                        decision_timeline=extraction.decision_timeline,
                        contact_preference=extraction.contact_preference,
                        version=1,
                        intent_status="NEW",
                        status_change_trigger="agent",
                    )
                    .on_conflict_do_update(
                        index_elements=["customer_id"],
                        set_={
                            "budget_range": extraction.budget_range,
                            "urgency_level": extraction.urgency_level.value
                            if extraction.urgency_level
                            else "UNKNOWN",
                            "product_interest": extraction.product_interest,
                            "decision_timeline": extraction.decision_timeline,
                            "contact_preference": extraction.contact_preference,
                            "version": IntentTracking.version + 1,
                            "status_change_trigger": "agent",
                            "updated_at": "now()",
                        },
                    )
                    .returning(IntentTracking)
                )

                result = await db.execute(stmt)
                row = result.scalar_one_or_none()

                if row:
                    logger.debug(
                        "Intent upserted",
                        extra={"customer_id": customer_id, "version": row.version},
                    )
                    return row

                # rowcount=0 indicates version conflict (concurrent update)
                if attempt < max_retries - 1:
                    delay_ms = backoff_delays[attempt]
                    logger.debug(
                        "Version conflict, retrying",
                        extra={
                            "customer_id": customer_id,
                            "attempt": attempt + 1,
                            "delay_ms": delay_ms,
                        },
                    )
                    await asyncio.sleep(delay_ms / 1000.0)
                else:
                    raise IntentLockConflictError(
                        f"Failed to upsert intent for {customer_id} after {max_retries} attempts"
                    )

            except Exception as e:
                if isinstance(e, IntentLockConflictError):
                    raise
                logger.error(
                    "Upsert failed with exception",
                    extra={"customer_id": customer_id, "error": str(e)},
                )
                raise

        raise IntentLockConflictError(
            f"Failed to upsert intent for {customer_id} after {max_retries} attempts"
        )

    async def update_status(
        self,
        customer_id: str,
        new_status: str,
        expected_version: int,
        trigger: str,
        db: AsyncSession,
    ) -> IntentTracking:
        """Update intent status with optimistic lock (version check).

        Args:
            customer_id: Unique customer identifier
            new_status: New intent_status value (e.g., "CONTACTED", "CONVERTED")
            expected_version: Expected current version (for optimistic lock)
            trigger: Who triggered the update ("admin", "agent", "system")
            db: AsyncSession for database operations

        Returns:
            Updated IntentTracking row

        Raises:
            OptimisticLockError: If expected_version doesn't match (HTTP 409)
            CustomerNotFoundError: If customer not found (HTTP 404)
        """
        stmt = (
            update(IntentTracking)
            .where(
                IntentTracking.customer_id == customer_id,
                IntentTracking.version == expected_version,
            )
            .values(
                intent_status=new_status,
                version=IntentTracking.version + 1,
                status_changed_at="now()",
                status_change_trigger=trigger,
                updated_at="now()",
            )
            .returning(IntentTracking)
        )

        result = await db.execute(stmt)
        row = result.scalar_one_or_none()

        if row:
            logger.debug(
                "Intent status updated",
                extra={
                    "customer_id": customer_id,
                    "new_status": new_status,
                    "version": row.version,
                },
            )
            return row

        # Check if customer exists at all
        check_stmt = select(IntentTracking).where(IntentTracking.customer_id == customer_id)
        existing = await db.execute(check_stmt)
        if not existing.scalar_one_or_none():
            raise CustomerNotFoundError(f"Customer {customer_id} not found")

        # Customer exists but version mismatch (optimistic lock conflict)
        raise IntentLockConflictError(
            f"Version mismatch for customer {customer_id} (expected {expected_version})"
        )
