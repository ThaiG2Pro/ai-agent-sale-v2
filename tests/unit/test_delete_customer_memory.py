"""Tests for DELETE /memory/customer/{customer_id} endpoint (Phase 8, T141-T150)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.main import app
from core.config import settings


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def admin_headers():
    """Admin authorization headers."""
    return {"X-Admin-Key": settings.X_ADMIN_KEY}


@pytest.mark.asyncio
async def test_delete_without_confirm_flag():
    """T143 (edge): DELETE without confirm=true → 400.

    Safety check to prevent accidental mass deletes.
    """
    from unittest.mock import AsyncMock

    from api.routes.memory import delete_customer_memory

    # Mock database
    mock_db = AsyncMock(spec=AsyncSession)

    # Mock require_admin_key to return True
    with patch("api.routes.memory.require_admin_key", return_value=True):
        with pytest.raises(Exception) as exc_info:
            # Call endpoint without confirm flag
            await delete_customer_memory(
                customer_id="cust_001",
                confirm=False,
                db=mock_db,
                _admin=True,
            )

        # Should raise HTTPException with 400
        assert exc_info.value.status_code == 400
        assert "confirm=true required" in exc_info.value.detail


@pytest.mark.asyncio
async def test_delete_invalid_customer_id():
    """T144 (edge): DELETE with invalid customer_id → 400.

    Prevents deletion of non-existent or malformed customer IDs.
    """
    from api.routes.memory import delete_customer_memory

    mock_db = AsyncMock(spec=AsyncSession)

    with patch("api.routes.memory.require_admin_key", return_value=True):
        with pytest.raises(Exception) as exc_info:
            await delete_customer_memory(
                customer_id="invalid_id",  # Missing "cust_" prefix
                confirm=True,
                db=mock_db,
                _admin=True,
            )

        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_delete_successful_cascade():
    """T145: DELETE with confirm=true → cascade delete all tables.

    Tests transactional deletion across IntentTracking, ConversationSummaries,
    SemanticMemory, and AuditLog.
    """
    from api.routes.memory import delete_customer_memory

    mock_db = AsyncMock(spec=AsyncSession)
    mock_db.execute = AsyncMock()
    mock_db.delete = AsyncMock()
    mock_db.add = AsyncMock()
    mock_db.commit = AsyncMock()

    # Mock count results
    def mock_execute(stmt):
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1
        mock_result.scalars.return_value.all.return_value = [
            MagicMock(),  # Dummy row for deletion
        ]
        return mock_result

    mock_db.execute = AsyncMock(side_effect=mock_execute)

    with patch("api.routes.memory.require_admin_key", return_value=True):
        result = await delete_customer_memory(
            customer_id="cust_001",
            confirm=True,
            db=mock_db,
            _admin=True,
        )

        # Verify result structure (T146)
        assert result["customer_id"] == "cust_001"
        assert result["status"] == "success"
        assert "deleted" in result
        assert result["deleted"]["intent_tracking"] == 1
        assert result["deleted"]["conversation_summaries"] == 1
        assert result["deleted"]["semantic_memory"] == 1


@pytest.mark.asyncio
async def test_delete_with_rollback_on_failure():
    """T149 (edge): Deletion mid-commit failure → rollback, nothing deleted.

    Ensures transactional integrity.
    """
    from api.routes.memory import delete_customer_memory

    mock_db = AsyncMock(spec=AsyncSession)
    mock_db.execute = AsyncMock(side_effect=RuntimeError("DB connection lost"))
    mock_db.rollback = AsyncMock()

    with patch("api.routes.memory.require_admin_key", return_value=True):
        with pytest.raises(Exception) as exc_info:
            await delete_customer_memory(
                customer_id="cust_001",
                confirm=True,
                db=mock_db,
                _admin=True,
            )

        # Should rollback on error
        mock_db.rollback.assert_called()
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_delete_zero_records():
    """T144 (edge): DELETE customer with no records → still succeeds with zeros.

    Cold-start scenario where customer exists but has no memory.
    """
    from api.routes.memory import delete_customer_memory

    mock_db = AsyncMock(spec=AsyncSession)

    def mock_execute(stmt):
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0  # No records
        mock_result.scalars.return_value.all.return_value = []  # Empty result set
        return mock_result

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()

    with patch("api.routes.memory.require_admin_key", return_value=True):
        result = await delete_customer_memory(
            customer_id="cust_999",
            confirm=True,
            db=mock_db,
            _admin=True,
        )

        # Should still return success with zero counts
        assert result["deleted"]["intent_tracking"] == 0
        assert result["deleted"]["conversation_summaries"] == 0
        assert result["deleted"]["semantic_memory"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
