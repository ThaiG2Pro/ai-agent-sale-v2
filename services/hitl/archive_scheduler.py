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


def _seconds_until_next_run(now: datetime, run_at_hour: int) -> float:
    """Seconds until the next occurrence of run_at_hour:00 UTC."""
    target = now.replace(hour=run_at_hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def run_nightly_archive(
    session_factory: async_sessionmaker[AsyncSession],
    batch_size: int = 1000,
    run_at_hour: int = 2,
) -> None:
    """Infinite loop to process QueuedMessage archiving (T070).

    Anchored to run_at_hour:00 UTC every night instead of sleep(24h) from
    process start, so restarts don't make the run time drift through the day.
    """
    logger.info(f"Starting HITL archive scheduler (nightly at {run_at_hour:02d}:00 UTC)...")
    while True:
        await asyncio.sleep(_seconds_until_next_run(datetime.now(UTC), run_at_hour))
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
