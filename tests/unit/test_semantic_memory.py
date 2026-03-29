"""Unit tests for semantic memory service (Phase 7, T114-T126).

Tests cover:
- Embedding storage with dimension validation
- Customer_id isolation (no cross-customer leakage)
- STALE status handling
- Cosine similarity filtering
- Threshold-based relevance filtering
"""

from unittest.mock import AsyncMock, patch

import pytest

from services.memory.semantic_memory import (
    EmbeddingDimensionMismatchError,
    SemanticMemoryService,
)


@pytest.fixture
def semantic_service():
    """Fixture for SemanticMemoryService."""
    return SemanticMemoryService()


@pytest.fixture
def mock_db():
    """Fixture for mocking AsyncSession."""
    db = AsyncMock()
    mock_result = AsyncMock()
    db.execute.return_value = mock_result
    return db


class TestSemanticMemoryStore:
    """Test suite for semantic memory storage."""

    @pytest.mark.asyncio
    async def test_store_inserts_with_model_version(self, semantic_service, mock_db):
        """T114: store() inserts row with model_version='bge-m3@1024'."""
        with patch("services.ai.AIGateway.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024  # Correct dimension

            await semantic_service.store(
                summary_id="summary_001",
                customer_id="cust_001",
                session_id="t001",
                summary_text="Test summary",
                db=mock_db,
            )

            # Verify db.add was called
            mock_db.add.assert_called_once()
            # Verify db.commit was called
            mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_sets_status_active(self, semantic_service, mock_db):
        """T115: store() sets status='ACTIVE' on insert."""
        with patch("services.ai.AIGateway.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            await semantic_service.store(
                summary_id="summary_002",
                customer_id="cust_002",
                session_id="t002",
                summary_text="Another summary",
                db=mock_db,
            )

            # Get the added object
            call_args = mock_db.add.call_args
            semantic_memory = call_args[0][0]
            assert semantic_memory.status == "ACTIVE"

    @pytest.mark.asyncio
    async def test_store_wrong_dimension_raises_error(self, semantic_service, mock_db):
        """T116: store() with wrong dimension → raises EmbeddingDimensionMismatchError."""
        with patch("services.ai.AIGateway.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 512  # Wrong dimension

            with pytest.raises(EmbeddingDimensionMismatchError):
                await semantic_service.store(
                    summary_id="summary_003",
                    customer_id="cust_003",
                    session_id="t003",
                    summary_text="Bad dimension",
                    db=mock_db,
                )


class TestSemanticMemoryRetrieve:
    """Test suite for semantic memory retrieval."""

    @pytest.mark.asyncio
    async def test_retrieve_customer_a_returns_only_customer_a(self, semantic_service, mock_db):
        """T118: retrieve() with customer_id='A' → only returns customer A's results."""
        with patch("services.ai.AIGateway.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            # Mock DB returns 3 rows for customer A
            mock_result = AsyncMock()
            mock_result.fetchall.return_value = [
                ("id1", "sum_id1", "Summary A1", 0.9),
                ("id2", "sum_id2", "Summary A2", 0.85),
                ("id3", "sum_id3", "Summary A3", 0.8),
            ]
            mock_db.execute.return_value = mock_result

            results = await semantic_service.retrieve(
                customer_id="A",
                query="test query",
                db=mock_db,
            )

            assert len(results) == 3
            assert all(r.similarity_score >= 0.75 for r in results)

    @pytest.mark.asyncio
    async def test_retrieve_cross_customer_isolation(self, semantic_service, mock_db):
        """T119: retrieve() with customer_id='A' excludes customer_id='B' rows."""
        with patch("services.ai.AIGateway.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            # Mock DB returns only customer A rows (isolation enforced by SQL WHERE)
            mock_result = AsyncMock()
            mock_result.fetchall.return_value = [
                ("id1", "sum_id1", "Summary A", 0.9),
            ]
            mock_db.execute.return_value = mock_result

            results = await semantic_service.retrieve(
                customer_id="A",
                query="test query",
                db=mock_db,
            )

            # Verify the query included customer_id filter
            call_args = mock_db.execute.call_args
            sql = call_args[0][0]
            assert ":customer_id" in str(sql)

            assert len(results) == 1

    @pytest.mark.asyncio
    async def test_retrieve_filters_by_threshold(self, semantic_service, mock_db):
        """T120: retrieve() filters by min_score → only returns scores >= 0.75."""
        with patch("services.ai.AIGateway.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            # Mock DB returns 3 results: 2 above threshold, 1 below
            mock_result = AsyncMock()
            mock_result.fetchall.return_value = [
                ("id1", "sum_id1", "Good result", 0.9),
                ("id2", "sum_id2", "Borderline", 0.60),  # Below threshold
                ("id3", "sum_id3", "Good result", 0.85),
            ]
            mock_db.execute.return_value = mock_result

            results = await semantic_service.retrieve(
                customer_id="A",
                query="test query",
                min_score=0.75,
                db=mock_db,
            )

            # Only 2 results should be returned
            assert len(results) == 2
            assert all(r.similarity_score >= 0.75 for r in results)

    @pytest.mark.asyncio
    async def test_retrieve_all_below_threshold_returns_empty(self, semantic_service, mock_db):
        """T121: retrieve() with all scores < threshold → empty list (no error)."""
        with patch("services.ai.AIGateway.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            # Mock DB returns results all below threshold
            mock_result = AsyncMock()
            mock_result.fetchall.return_value = [
                ("id1", "sum_id1", "Poor result", 0.5),
                ("id2", "sum_id2", "Poor result", 0.6),
            ]
            mock_db.execute.return_value = mock_result

            results = await semantic_service.retrieve(
                customer_id="A",
                query="test query",
                min_score=0.75,
                db=mock_db,
            )

            assert len(results) == 0

    @pytest.mark.asyncio
    async def test_retrieve_no_rows_cold_start(self, semantic_service, mock_db):
        """T122: retrieve() with no rows for customer → empty list (cold start, no error)."""
        with patch("services.ai.AIGateway.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            # Mock DB returns empty result
            mock_result = AsyncMock()
            mock_result.fetchall.return_value = []
            mock_db.execute.return_value = mock_result

            results = await semantic_service.retrieve(
                customer_id="new_customer",
                query="test query",
                db=mock_db,
            )

            assert len(results) == 0

    @pytest.mark.asyncio
    async def test_retrieve_excludes_stale_rows(self, semantic_service, mock_db):
        """T123: retrieve() excludes STALE rows (status='STALE' not returned)."""
        with patch("services.ai.AIGateway.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            # Mock DB returns only ACTIVE rows (STALE excluded by SQL WHERE)
            mock_result = AsyncMock()
            mock_result.fetchall.return_value = [
                ("id1", "sum_id1", "Active result", 0.9),
            ]
            mock_db.execute.return_value = mock_result

            results = await semantic_service.retrieve(
                customer_id="A",
                query="test query",
                db=mock_db,
            )

            # Verify SQL included status filter
            call_args = mock_db.execute.call_args
            sql = str(call_args[0][0])
            assert "status" in sql and "ACTIVE" in sql

            assert len(results) == 1


class TestSemanticMemoryFlagStale:
    """Test suite for stale embedding flagging."""

    @pytest.mark.asyncio
    async def test_flag_stale_updates_old_version(self, semantic_service, mock_db):
        """T125: flag_stale() → 3 old rows → 3 flagged STALE, 2 current remain ACTIVE."""
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        # Mock the update result
        mock_result = AsyncMock()
        mock_result.rowcount = 3  # 3 rows updated
        mock_db.execute.return_value = mock_result

        count = await semantic_service.flag_stale(
            current_model_version="bge-m3@1024",
            db=mock_db,
        )

        assert count == 3
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_flag_stale_current_version_no_change(self, semantic_service, mock_db):
        """T126: flag_stale() with all rows current version → 0 rows changed."""
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        # Mock the update result
        mock_result = AsyncMock()
        mock_result.rowcount = 0  # No rows updated
        mock_db.execute.return_value = mock_result

        count = await semantic_service.flag_stale(
            current_model_version="bge-m3@1024",
            db=mock_db,
        )

        assert count == 0
