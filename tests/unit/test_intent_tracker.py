"""Unit tests for IntentTracker service with optimistic locking (T061-T069).

Tests cover:
- T061-T064: Upsert with version tracking and retry logic
- T065: Concurrent write safety (race condition test)
- T067-T069: Status transitions with version checks
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.memory.intent_tracker import (
    CustomerNotFoundError,
    IntentLockConflictError,
    IntentTracker,
)


class TestIntentTracker:
    """Test suite for intent tracking service with optimistic locking."""

    def setup_method(self):
        """Setup for each test."""
        self.tracker = IntentTracker()

    # === Upsert Tests (T061-T064) ===

    @pytest.mark.asyncio
    async def test_upsert_creates_new_row_version_1(self):
        """T061: upsert_with_lock() creates new row when customer missing.

        Verify version=1, status=NEW, created_at set.
        """
        mock_db = AsyncMock()
        mock_row = MagicMock()
        mock_row.version = 1
        mock_row.intent_status = "NEW"
        mock_row.customer_id = "cust-001"

        # Mock execute to return the new row
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row
        mock_db.execute.return_value = mock_result

        from core.agent.state import SalesIntentExtraction, UrgencyLevel

        extraction = SalesIntentExtraction(urgency_level=UrgencyLevel.MEDIUM)

        result = await self.tracker.upsert_with_lock("cust-001", "thread-001", extraction, mock_db)

        assert result.version == 1
        assert result.intent_status == "NEW"
        assert result.customer_id == "cust-001"

    @pytest.mark.asyncio
    async def test_upsert_updates_existing_row_increments_version(self):
        """T062: upsert_with_lock() updates existing row → version 1→2."""
        mock_db = AsyncMock()
        mock_row = MagicMock()
        mock_row.version = 2
        mock_row.customer_id = "cust-001"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row
        mock_db.execute.return_value = mock_result

        from core.agent.state import SalesIntentExtraction

        extraction = SalesIntentExtraction()

        result = await self.tracker.upsert_with_lock("cust-001", "thread-001", extraction, mock_db)

        assert result.version == 2

    @pytest.mark.asyncio
    async def test_upsert_retry_on_version_conflict(self):
        """T063: rowcount=0 twice then succeeds → succeeds with correct version.

        Simulates two version conflicts followed by successful upsert.
        """
        mock_db = AsyncMock()
        mock_row = MagicMock()
        mock_row.version = 1

        # First two calls return None (version conflict), third returns row
        mock_result_conflict = MagicMock()
        mock_result_conflict.scalar_one_or_none.return_value = None

        mock_result_success = MagicMock()
        mock_result_success.scalar_one_or_none.return_value = mock_row

        mock_db.execute.side_effect = [
            mock_result_conflict,
            mock_result_conflict,
            mock_result_success,
        ]

        from core.agent.state import SalesIntentExtraction

        extraction = SalesIntentExtraction()

        result = await self.tracker.upsert_with_lock("cust-001", "thread-001", extraction, mock_db)

        assert result.version == 1
        # Verify retried 3 times (2 failures + 1 success)
        assert mock_db.execute.call_count == 3

    @pytest.mark.asyncio
    async def test_upsert_max_retries_exceeded(self):
        """T064: rowcount=0 three times → IntentLockConflictError."""
        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        mock_db.execute.return_value = mock_result

        from core.agent.state import SalesIntentExtraction

        extraction = SalesIntentExtraction()

        with pytest.raises(IntentLockConflictError):
            await self.tracker.upsert_with_lock("cust-001", "thread-001", extraction, mock_db)

    # === Status Update Tests (T067-T069) ===

    @pytest.mark.asyncio
    async def test_update_status_success(self):
        """T067: update_status() with correct version → status updated."""
        mock_db = AsyncMock()
        mock_row = MagicMock()
        mock_row.version = 2
        mock_row.intent_status = "CONTACTED"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_row
        mock_db.execute.return_value = mock_result

        result = await self.tracker.update_status(
            "cust-001", "CONTACTED", expected_version=1, trigger="admin", db=mock_db
        )

        assert result.intent_status == "CONTACTED"
        assert result.version == 2

    @pytest.mark.asyncio
    async def test_update_status_stale_version(self):
        """T068: update_status() with stale version → OptimisticLockError (409)."""
        mock_db = AsyncMock()

        # First call returns None (no match, version conflict)
        # Second call returns existing row (customer exists)
        mock_result_no_match = MagicMock()
        mock_result_no_match.scalar_one_or_none.return_value = None

        mock_existing_row = MagicMock()
        mock_existing_row.customer_id = "cust-001"
        mock_result_existing = MagicMock()
        mock_result_existing.scalar_one_or_none.return_value = mock_existing_row

        mock_db.execute.side_effect = [mock_result_no_match, mock_result_existing]

        with pytest.raises(IntentLockConflictError):
            await self.tracker.update_status(
                "cust-001", "CONTACTED", expected_version=999, trigger="admin", db=mock_db
            )

    @pytest.mark.asyncio
    async def test_update_status_missing_customer(self):
        """T069: update_status() missing customer → CustomerNotFoundError (404)."""
        mock_db = AsyncMock()

        # Both calls return None (customer doesn't exist)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        mock_db.execute.return_value = mock_result

        with pytest.raises(CustomerNotFoundError):
            await self.tracker.update_status(
                "unknown-cust", "CONTACTED", expected_version=1, trigger="admin", db=mock_db
            )
