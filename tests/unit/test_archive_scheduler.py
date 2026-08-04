"""Unit tests for services/hitl/archive_scheduler.py (FR-021, T070).

Covers the schedule math, the batch-archive DB logic, and one loop
iteration of the nightly scheduler (commit on success, survive errors).
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.hitl.archive_scheduler import (
    _archive_messages,
    _seconds_until_next_run,
    run_nightly_archive,
)

# --- _seconds_until_next_run ---------------------------------------------


def test_seconds_until_next_run_same_day():
    """Before run hour → target is later today."""
    now = datetime(2026, 8, 4, 0, 30, tzinfo=UTC)
    assert _seconds_until_next_run(now, run_at_hour=2) == pytest.approx(90 * 60)


def test_seconds_until_next_run_rolls_to_next_day():
    """At/after run hour → target is tomorrow (no drift from process start)."""
    now = datetime(2026, 8, 4, 2, 0, 0, tzinfo=UTC)
    assert _seconds_until_next_run(now, run_at_hour=2) == pytest.approx(24 * 3600)

    now = datetime(2026, 8, 4, 23, 0, tzinfo=UTC)
    assert _seconds_until_next_run(now, run_at_hour=2) == pytest.approx(3 * 3600)


# --- _archive_messages -----------------------------------------------------


def _db_returning_ids(ids: list[int]) -> AsyncMock:
    db = AsyncMock()
    select_result = MagicMock()
    select_result.scalars.return_value.all.return_value = ids
    db.execute.return_value = select_result
    return db


@pytest.mark.asyncio
async def test_archive_messages_no_candidates_returns_zero():
    db = _db_returning_ids([])
    count = await _archive_messages(db, batch_size=1000)
    assert count == 0
    # Only the SELECT ran — no UPDATE issued for an empty batch.
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_archive_messages_updates_and_returns_count():
    db = _db_returning_ids([1, 2, 3])
    count = await _archive_messages(db, batch_size=1000)
    assert count == 3
    # SELECT + UPDATE
    assert db.execute.await_count == 2


# --- run_nightly_archive (one iteration) -----------------------------------


def _session_factory_for(db: AsyncMock) -> MagicMock:
    ctx = AsyncMock()
    ctx.__aenter__.return_value = db
    ctx.__aexit__.return_value = False
    return MagicMock(return_value=ctx)


@pytest.mark.asyncio
async def test_nightly_archive_commits_after_archiving():
    """One loop iteration: archive → commit; second sleep cancels the loop."""
    db = _db_returning_ids([10, 11])
    factory = _session_factory_for(db)

    with patch(
        "services.hitl.archive_scheduler.asyncio.sleep",
        AsyncMock(side_effect=[None, asyncio.CancelledError()]),
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_nightly_archive(factory, batch_size=500)

    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_nightly_archive_survives_db_errors():
    """A failing iteration is logged, not raised — the scheduler keeps looping."""
    db = AsyncMock()
    db.execute.side_effect = RuntimeError("db down")
    factory = _session_factory_for(db)

    with patch(
        "services.hitl.archive_scheduler.asyncio.sleep",
        AsyncMock(side_effect=[None, asyncio.CancelledError()]),
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_nightly_archive(factory)

    db.commit.assert_not_awaited()
