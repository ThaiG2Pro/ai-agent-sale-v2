"""Semantic memory service for cross-session memory search (Phase 7, T112+).

Why: Customers should see relevant past summaries from previous conversations.
What:
- Store summary embeddings with metadata (customer_id, status, model_version)
- Retrieve top-K similar summaries for a given customer
- Handle embedding model versioning (FR-010b: stale embeddings not returned)
- Guard against memory leakage (strict customer_id isolation)

Functional Requirements:
- FR-007: Store summary embeddings in semantic_memory table
- FR-008b: Customer_id isolation (no cross-customer leakage)
- FR-009: 500ms latency target for retrieval
- FR-010b: Embedding model versioning (flag old embeddings STALE)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import text, update

from core.config import settings
from models.schema import EmbeddingStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class EmbeddingDimensionMismatchError(ValueError):
    """Raised when embedding dimension doesn't match configured dimension."""

    pass


class SemanticMemoryResult:
    """Result from semantic memory retrieval."""

    def __init__(
        self,
        summary_id: str,
        summary_text: str,
        similarity_score: float,
        session_id: str,
    ):
        self.summary_id = summary_id
        self.summary_text = summary_text
        self.similarity_score = similarity_score
        self.session_id = session_id

    def __repr__(self) -> str:
        return f"SemanticMemoryResult(id={self.summary_id}, score={self.similarity_score:.2f})"


class SemanticMemoryService:
    """Service for semantic memory storage and retrieval (FR-007, FR-008b, FR-010b)."""

    # Thresholds from spec
    MIN_SIMILARITY_SCORE = 0.75  # FR-007: context drift guard
    EMBEDDING_BATCH_SIZE = 100  # Process 100 embeddings at a time

    def __init__(self):
        """Initialize semantic memory service."""
        self.embed_model = settings.EMBED_MODEL
        self.embed_dimension = settings.EMBED_DIMENSION
        self.model_version = f"{self.embed_model}@{self.embed_dimension}"

    async def store(
        self,
        summary_id: str,
        customer_id: str,
        session_id: str,
        summary_text: str,
        db: AsyncSession,
    ) -> str:
        """Store summary embedding in semantic_memory (T113).

        Args:
            summary_id: UUID of conversation_summaries row
            customer_id: Customer identifier (FR-008b: isolation)
            session_id: LangGraph thread_id
            summary_text: Raw summary text to embed
            db: Async database session

        Returns:
            UUID of inserted semantic_memory row

        Raises:
            EmbeddingDimensionMismatchError: If embedding dimension doesn't match
        """
        try:
            # Embed the summary text
            from services.ai import AIGateway

            embedding = await AIGateway.embed(summary_text)

            # Validate dimension (T116 guard)
            if len(embedding) != self.embed_dimension:
                raise EmbeddingDimensionMismatchError(
                    f"Embedding dimension {len(embedding)} != configured {self.embed_dimension}"
                )

            # Insert to semantic_memory with ACTIVE status (T115)
            from models.schema import SemanticMemory

            semantic_memory = SemanticMemory(
                summary_id=summary_id,
                customer_id=customer_id,
                embedding=embedding,
                embedding_model=self.embed_model,
                embedding_dimension=self.embed_dimension,
                status=EmbeddingStatus.ACTIVE,  # T115: Always ACTIVE on creation
            )
            db.add(semantic_memory)
            await db.commit()

            logger.debug(
                "Semantic memory stored",
                extra={
                    "summary_id": str(summary_id),
                    "customer_id": customer_id,
                    "session_id": session_id,
                    "model_version": self.model_version,
                    "status": "ACTIVE",
                },
            )

            return str(semantic_memory.id)

        except EmbeddingDimensionMismatchError:
            raise
        except Exception as e:
            logger.error(
                "Failed to store semantic memory",
                extra={
                    "summary_id": str(summary_id),
                    "customer_id": customer_id,
                    "error": str(e),
                },
            )
            raise

    async def retrieve(
        self,
        customer_id: str,
        query: str,
        top_k: int = 3,
        min_score: float = MIN_SIMILARITY_SCORE,
        db: AsyncSession | None = None,
    ) -> list[SemanticMemoryResult]:
        """Retrieve top-K similar semantic memories for a customer (T117).

        Strict customer_id isolation (T119: R5 memory leakage guard).
        Filters STALE embeddings (T123: FR-010b).
        Applies cosine similarity threshold (T120: R7 context drift guard).

        Args:
            customer_id: Customer identifier (strict isolation)
            query: User query string to find similar summaries
            top_k: Number of results to return (adaptive per spec)
            min_score: Cosine similarity threshold (default 0.75)
            db: Async database session

        Returns:
            List of SemanticMemoryResult ordered by similarity (high to low)

        Empty list if:
        - No results found (T122: cold start)
        - All scores below threshold (T121)
        - No ACTIVE rows for this customer (T123)
        """
        if not db:
            logger.warning("retrieve() called without db session")
            return []

        try:
            # Embed the query
            from services.ai import AIGateway

            query_embedding = await AIGateway.embed(query)

            if len(query_embedding) != self.embed_dimension:
                logger.warning(
                    "Query embedding dimension mismatch",
                    extra={
                        "expected": self.embed_dimension,
                        "got": len(query_embedding),
                    },
                )
                return []

            # Cosine search with strict customer_id isolation (T117, T119)
            # WHERE customer_id = :cid AND status = 'ACTIVE' AND embedding_model = :model
            query_sql = text(
                """
            SELECT sm.id, sm.summary_id, cs.summary_text,
                   1 - (sm.embedding <=> :vec)::numeric AS cosine_score
            FROM agent_v1.semantic_memory sm
            JOIN agent_v1.conversation_summaries cs
              ON sm.summary_id = cs.id
            WHERE sm.customer_id = :customer_id
              AND sm.status = 'ACTIVE'
              AND sm.embedding_model = :embedding_model
            ORDER BY sm.embedding <=> :vec
            LIMIT :top_k
            """
            )

            result = await db.execute(
                query_sql,
                {
                    "customer_id": customer_id,
                    "vec": query_embedding,
                    "embedding_model": self.embed_model,
                    "top_k": top_k,
                },
            )

            rows = result.fetchall()

            # Filter by threshold in Python (T120: apply min_score filter)
            results = []
            for row in rows:
                score = float(row[3])  # cosine_score
                if score >= min_score:
                    results.append(
                        SemanticMemoryResult(
                            summary_id=str(row[1]),
                            summary_text=row[2],
                            similarity_score=score,
                            session_id="",  # Not used; kept for compatibility
                        )
                    )

            logger.debug(
                "Semantic memory retrieved",
                extra={
                    "customer_id": customer_id,
                    "query_length": len(query),
                    "results_count": len(results),
                    "min_score": min_score,
                },
            )

            return results

        except Exception as e:
            logger.error(
                "Failed to retrieve semantic memory",
                extra={
                    "customer_id": customer_id,
                    "error": str(e),
                },
            )
            return []  # Graceful degradation

    async def flag_stale(
        self,
        current_embedding_model: str,
        db: AsyncSession,
    ) -> int:
        """Mark old embeddings as STALE when model version changes (T124, FR-010b).

        Called when embedding model is updated. Flags all rows with different
        embedding_model but keeps them for audit trail.

        Args:
            current_embedding_model: Current embedding model name (e.g., "bge-m3")
            db: Async database session

        Returns:
            Count of rows updated to STALE status
        """
        try:
            from models.schema import SemanticMemory

            # UPDATE status='STALE' WHERE embedding_model != :model AND status = 'ACTIVE'
            stmt = (
                update(SemanticMemory)
                .where(
                    (SemanticMemory.embedding_model != current_embedding_model)
                    & (SemanticMemory.status == EmbeddingStatus.ACTIVE)
                )
                .values(status=EmbeddingStatus.STALE)
            )

            result = await db.execute(stmt)
            await db.commit()

            count = result.rowcount or 0

            logger.info(
                "Embeddings flagged STALE",
                extra={
                    "new_embedding_model": current_embedding_model,
                    "rows_updated": count,
                },
            )

            return count

        except Exception as e:
            logger.error(
                "Failed to flag stale embeddings",
                extra={
                    "current_embedding_model": current_embedding_model,
                    "error": str(e),
                },
            )
            raise
