"""
Why this exists: Background task for HITL timeouts (Article III, V).
What it does: Polls database for sessions paused longer than thresholds.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select

from core.config import settings
from models.schema import HITLMetadata
from services.hitl.service import HITLService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


logger = logging.getLogger(__name__)


async def run_timeout_scheduler(
    session_factory: async_sessionmaker[AsyncSession],
    poll_interval_seconds: int = 60,
) -> None:
    """Infinite loop to process HITL timeouts (T047)."""
    logger.info("Starting HITL timeout scheduler...")
    while True:
        try:
            async with session_factory() as db:
                await _process_timeouts(db)
                await db.commit()
        except Exception as e:
            logger.exception(f"Error in timeout scheduler: {e}")

        await asyncio.sleep(poll_interval_seconds)


async def _process_timeouts(db: AsyncSession) -> None:
    """Queries and processes sessions exceeding warn/escalate thresholds (T048, T049)."""
    now = datetime.now(UTC)
    warn_threshold = now - timedelta(minutes=settings.HITL_TIMEOUT_WARN_MIN)
    escalate_threshold = now - timedelta(minutes=settings.HITL_TIMEOUT_ESCALATE_MIN)

    # 1. T048: Handle Warnings (30 min)
    stmt_warn = select(HITLMetadata).where(
        HITLMetadata.status == "paused",
        HITLMetadata.paused_at < warn_threshold,
        HITLMetadata.timeout_notified_at == None,  # noqa: E711
    )
    res_warn = await db.execute(stmt_warn)
    to_warn = res_warn.scalars().all()

    for meta in to_warn:
        logger.warning(
            f"HITL Timeout Warning: Session {meta.session_id} has been paused "
            f"since {meta.paused_at}. Support link: {settings.SUPPORT_CONTACT_LINK}"
        )
        # Structured log for observability (T048)
        logger.info(
            "HITL event",
            extra={
                "event": "hitl_timeout_warn",
                "session_id": meta.session_id,
                "paused_at": meta.paused_at.isoformat(),
            },
        )
        meta.timeout_notified_at = now

    # 2. T049: Handle Escalation (60 min)
    stmt_escalate = select(HITLMetadata).where(
        HITLMetadata.status == "paused",
        HITLMetadata.paused_at < escalate_threshold,
    )
    res_escalate = await db.execute(stmt_escalate)
    to_escalate = res_escalate.scalars().all()

    for meta in to_escalate:
        logger.info(
            "HITL event",
            extra={
                "event": "hitl_timeout_escalate",
                "session_id": meta.session_id,
            },
        )
        await HITLService.escalate_to_support(meta.session_id, db)
