"""
Why this exists: Background task for data retention policies (Article VII).
What it does: Marks old processed messages as archived every 24 hours.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select, update

from models.schema import QueuedMessage

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


logger = logging.getLogger(__name__)


async def run_nightly_archive(
    session_factory: async_sessionmaker[AsyncSession],
    batch_size: int = 1000,
    interval_hours: int = 24,
) -> None:
    """Infinite loop to process QueuedMessage archiving (T070)."""
    logger.info("Starting HITL archive scheduler...")
    while True:
        try:
            async with session_factory() as db:
                count = await _archive_messages(db, batch_size)
                await db.commit()
                if count > 0:
                    logger.info(
                        "HITL event",
                        extra={
                            "event": "nightly_archive",
                            "count": count,
                            "timestamp": datetime.now(UTC).isoformat(),
                        },
                    )
        except Exception as e:
            logger.exception(f"Error in archive scheduler: {e}")

        await asyncio.sleep(interval_hours * 3600)


async def _archive_messages(db: AsyncSession, batch_size: int) -> int:
    """Marks messages older than 90 days as archived (FR-021)."""
    threshold = datetime.now(UTC) - timedelta(days=90)

    # Find IDs to archive (limit to batch_size)
    stmt = (
        select(QueuedMessage.message_id)
        .where(
            QueuedMessage.processed == True,  # noqa: E712
            QueuedMessage.received_at < threshold,
            QueuedMessage.archived == False,  # noqa: E712
        )
        .limit(batch_size)
    )
    res = await db.execute(stmt)
    ids = res.scalars().all()

    if not ids:
        return 0

    # Batch update
    update_stmt = (
        update(QueuedMessage).where(QueuedMessage.message_id.in_(ids)).values(archived=True)
    )
    await db.execute(update_stmt)
    return len(ids)
