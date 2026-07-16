"""Tests for DELETE /memory/customer/{customer_id} endpoint (Phase 8, T141-T150).

RTBF must delete from ALL tables holding customer data (P0-2):
intent_tracking, sales_intent_logs, conversation_summaries, semantic_memory,
plus the LangGraph checkpoint tables — and must accept real-world IDs
(Telegram "tg:123" / numeric chat_id), not only the "cust_" prefix.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from api.main import app
from core.config import settings

ALL_DELETED_TABLES = {
    "intent_tracking",
    "sales_intent_logs",
    "conversation_summaries",
    "semantic_memory",
    "checkpoints",
    "checkpoint_writes",
    "checkpoint_blobs",
}


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def admin_headers():
    """Admin authorization headers."""
    return {"X-Admin-Key": settings.X_ADMIN_KEY}


def make_mock_db(*, thread_ids: list[str], rowcount: int) -> AsyncMock:
    """AsyncSession mock covering the RTBF statement sequence.

    Every execute() returns a result whose scalars().all() yields thread_ids
    (thread collection selects), rowcount == rowcount (bulk deletes) and
    scalar() is non-None (to_regclass existence probe).
    """
    mock_db = AsyncMock(spec=AsyncSession)

    def mock_execute(*args, **kwargs):
        result = MagicMock()
        result.scalars.return_value.all.return_value = thread_ids
        result.rowcount = rowcount
        result.scalar.return_value = "checkpoints"  # to_regclass: table exists
        return result

    mock_db.execute = AsyncMock(side_effect=mock_execute)
    mock_db.commit = AsyncMock()
    mock_db.rollback = AsyncMock()
    return mock_db


@pytest.mark.asyncio
async def test_delete_without_confirm_flag():
    """T143 (edge): DELETE without confirm=true → 400.

    Safety check to prevent accidental mass deletes.
    """
    from api.routes.memory import delete_customer_memory

    mock_db = AsyncMock(spec=AsyncSession)

    with patch("api.routes.memory.require_admin_key", return_value=True):
        with pytest.raises(Exception) as exc_info:
            await delete_customer_memory(
                customer_id="cust_001",
                confirm=False,
                db=mock_db,
                _admin=True,
            )

        assert exc_info.value.status_code == 400
        assert "confirm=true required" in exc_info.value.detail


@pytest.mark.asyncio
async def test_delete_blank_customer_id():
    """T144 (edge): DELETE with blank/whitespace customer_id → 400."""
    from api.routes.memory import delete_customer_memory

    mock_db = AsyncMock(spec=AsyncSession)

    with patch("api.routes.memory.require_admin_key", return_value=True):
        with pytest.raises(Exception) as exc_info:
            await delete_customer_memory(
                customer_id="   ",
                confirm=True,
                db=mock_db,
                _admin=True,
            )

        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_delete_accepts_telegram_style_ids():
    """Real customer IDs have no "cust_" prefix (Telegram "tg:123", numeric
    chat_id) — the old prefix gate made RTBF unusable for them."""
    from api.routes.memory import delete_customer_memory

    for customer_id in ("tg:123456", "987654321"):
        mock_db = make_mock_db(thread_ids=["telegram_987654321"], rowcount=1)

        with patch("api.routes.memory.require_admin_key", return_value=True):
            result = await delete_customer_memory(
                customer_id=customer_id,
                confirm=True,
                db=mock_db,
                _admin=True,
            )

        assert result["status"] == "success"
        assert result["customer_id"] == customer_id


@pytest.mark.asyncio
async def test_delete_successful_cascade():
    """T145: DELETE with confirm=true → cascade delete across ALL 7 tables.

    intent_tracking, sales_intent_logs, conversation_summaries,
    semantic_memory + the 3 LangGraph checkpoint tables.
    """
    from api.routes.memory import delete_customer_memory

    mock_db = make_mock_db(thread_ids=["thread_1"], rowcount=1)

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
    assert set(result["deleted"].keys()) == ALL_DELETED_TABLES
    for table in ALL_DELETED_TABLES:
        assert result["deleted"][table] == 1, f"{table} must be deleted"
    mock_db.commit.assert_awaited_once()


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

        mock_db.rollback.assert_called()
        assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_delete_zero_records():
    """T144 (edge): DELETE customer with no records → still succeeds with zeros.

    Cold-start scenario where customer exists but has no memory. With no
    thread_ids on record the checkpoint tables are not touched (counts 0).
    """
    from api.routes.memory import delete_customer_memory

    mock_db = make_mock_db(thread_ids=[], rowcount=0)

    with patch("api.routes.memory.require_admin_key", return_value=True):
        result = await delete_customer_memory(
            customer_id="cust_999",
            confirm=True,
            db=mock_db,
            _admin=True,
        )

    assert set(result["deleted"].keys()) == ALL_DELETED_TABLES
    assert all(count == 0 for count in result["deleted"].values())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
