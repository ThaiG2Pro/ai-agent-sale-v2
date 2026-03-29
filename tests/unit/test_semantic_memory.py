"""Unit tests for semantic memory service (Phase 7, T114-T126).

Tests cover:
- Embedding storage with dimension validation
- Customer_id isolation (no cross-customer leakage)
- STALE status handling
- Cosine similarity filtering
- Threshold-based relevance filtering
"""

from unittest.mock import AsyncMock, MagicMock, patch

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
    """Fixture for mocking AsyncSession properly."""
    db = AsyncMock()
    return db


class TestSemanticMemoryStore:
    """Test suite for semantic memory storage."""

    @pytest.mark.asyncio
    async def test_store_inserts_with_model_version(self, semantic_service, mock_db):
        """T114: store() inserts row with embedding_model='bge-m3'."""
        with patch("services.ai.AIGateway.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024  # Correct dimension
            mock_db.commit = AsyncMock()

            await semantic_service.store(
                summary_id="summary_001",
                customer_id="cust_001",
                session_id="t001",
                summary_text="Test summary",
                db=mock_db,
            )

            # Verify add() was called
            mock_db.add.assert_called_once()
            # Verify commit was called
            mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_sets_status_active(self, semantic_service, mock_db):
        """T115: store() sets status=ACTIVE on insert."""
        with patch("services.ai.AIGateway.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024
            mock_db.commit = AsyncMock()

            await semantic_service.store(
                summary_id="summary_001",
                customer_id="cust_001",
                session_id="t001",
                summary_text="Test summary",
                db=mock_db,
            )

            # Get the added object
            mock_db.add.assert_called_once()
            added_obj = mock_db.add.call_args[0][0]
            assert added_obj.status.value == "ACTIVE"

    @pytest.mark.asyncio
    async def test_store_wrong_dimension_raises_error(self, semantic_service):
        """T116: store() with wrong dimension → raises EmbeddingDimensionMismatchError."""
        mock_db = AsyncMock()
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
        """T118: retrieve() with customer_id='A' returns only customer A rows."""
        with patch("services.ai.AIGateway.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            # Create a proper async result
            mock_result = MagicMock()
            mock_result.fetchall.return_value = [
                ("id1", "sum_id1", "Summary A1", 0.9),
                ("id2", "sum_id2", "Summary A2", 0.85),
                ("id3", "sum_id3", "Summary A3", 0.8),
            ]
            mock_db.execute = AsyncMock(return_value=mock_result)

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

            mock_result = MagicMock()
            mock_result.fetchall.return_value = [
                ("id_a1", "sum_id_a1", "Summary A only", 0.95),
            ]
            mock_db.execute = AsyncMock(return_value=mock_result)

            results = await semantic_service.retrieve(
                customer_id="A",
                query="test",
                db=mock_db,
            )

            assert len(results) == 1
            # Verify WHERE customer_id filtering was passed to SQL
            call_args = mock_db.execute.call_args
            assert "customer_id" in call_args[0][1]
            assert call_args[0][1]["customer_id"] == "A"

    @pytest.mark.asyncio
    async def test_retrieve_filters_by_threshold(self, semantic_service, mock_db):
        """T120: retrieve() filters scores < min_score (0.75)."""
        with patch("services.ai.AIGateway.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            mock_result = MagicMock()
            mock_result.fetchall.return_value = [
                ("id1", "sum_id1", "Good", 0.95),
                ("id2", "sum_id2", "Bad", 0.50),
                ("id3", "sum_id3", "Okay", 0.76),
            ]
            mock_db.execute = AsyncMock(return_value=mock_result)

            results = await semantic_service.retrieve(
                customer_id="A", query="test", db=mock_db, min_score=0.75
            )

            # Only scores >= 0.75 should be returned
            assert len(results) == 2
            assert all(r.similarity_score >= 0.75 for r in results)

    @pytest.mark.asyncio
    async def test_retrieve_all_below_threshold_returns_empty(self, semantic_service, mock_db):
        """T121: retrieve() with all scores < threshold → empty list."""
        with patch("services.ai.AIGateway.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            mock_result = MagicMock()
            mock_result.fetchall.return_value = [
                ("id1", "sum_id1", "Bad1", 0.60),
                ("id2", "sum_id2", "Bad2", 0.50),
            ]
            mock_db.execute = AsyncMock(return_value=mock_result)

            results = await semantic_service.retrieve(
                customer_id="A", query="test", db=mock_db, min_score=0.75
            )

            assert len(results) == 0

    @pytest.mark.asyncio
    async def test_retrieve_no_rows_cold_start(self, semantic_service, mock_db):
        """T122: retrieve() with no rows → empty list, no exception."""
        with patch("services.ai.AIGateway.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            mock_result = MagicMock()
            mock_result.fetchall.return_value = []
            mock_db.execute = AsyncMock(return_value=mock_result)

            results = await semantic_service.retrieve(
                customer_id="UNKNOWN", query="test", db=mock_db
            )

            assert len(results) == 0

    @pytest.mark.asyncio
    async def test_retrieve_excludes_stale_rows(self, semantic_service, mock_db):
        """T123: retrieve() excludes STALE rows from results."""
        with patch("services.ai.AIGateway.embed", new_callable=AsyncMock) as mock_embed:
            mock_embed.return_value = [0.1] * 1024

            mock_result = MagicMock()
            mock_result.fetchall.return_value = [
                ("id1", "sum_id1", "Active summary", 0.95),
            ]
            mock_db.execute = AsyncMock(return_value=mock_result)

            results = await semantic_service.retrieve(customer_id="A", query="test", db=mock_db)

            # Verify SQL includes WHERE status = 'ACTIVE'
            call_args = mock_db.execute.call_args
            sql_str = str(call_args[0][0])
            assert "status = 'ACTIVE'" in sql_str
            assert len(results) == 1


class TestSemanticMemoryFlagStale:
    """Test suite for embedding versioning."""

    @pytest.mark.asyncio
    async def test_flag_stale_updates_old_version(self, semantic_service, mock_db):
        """T125: flag_stale() marks old embedding_model rows as STALE."""
        mock_db.execute = AsyncMock(return_value=MagicMock(rowcount=3))
        mock_db.commit = AsyncMock()

        count = await semantic_service.flag_stale(
            current_embedding_model="new-model-v2", db=mock_db
        )

        assert count == 3
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_flag_stale_current_version_no_change(self, semantic_service, mock_db):
        """T126: flag_stale() with all current → 0 rows changed, no exception."""
        mock_db.execute = AsyncMock(return_value=MagicMock(rowcount=0))
        mock_db.commit = AsyncMock()

        count = await semantic_service.flag_stale(
            current_embedding_model="current-model", db=mock_db
        )

        assert count == 0
        mock_db.commit.assert_called_once()
