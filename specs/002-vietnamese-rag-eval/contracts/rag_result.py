"""Why this exists: Canonical type contract for the RAG pipeline output.
What it does: Defines the RAGResult Pydantic model used as the return type of
             answer_with_rag(). This file is the authoritative source for the
             contract — the implementation in services/rag.py must match.

FR-007, FR-010, FR-011, FR-013 — constitution Article VI (Schema-First).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class RAGResult(BaseModel):
    """Structured output of the full Week 2 RAG pipeline.

    All fields are mandatory. `declined=True` means the confidence guard fired
    and `answer` contains the Vietnamese decline message, not a product answer.
    """

    # Core response
    answer: str
    """Generated answer text, or DECLINE_MESSAGE if declined=True."""

    declined: bool
    """True if the confidence guard (similarity < 0.7) or edge-case guard fired."""

    # Citation metadata (FR-011, Article IX)
    citations: list[dict[str, Any]]
    """List of source citations: [{product_id, chunk_id, sku, name}].
    Empty when declined=True."""

    # Retrieval quality signals (FR-010)
    best_similarity: float
    """Highest cosine similarity score across all retrieved chunks (pre-compression).
    Used for confidence guard comparison and observability."""

    similarity_gap: float
    """Difference between top-1 and top-2 vector similarity scores.
    Large gap (>0.15) = clear winner, high confidence.
    Small gap (<0.01) = ambiguous/duplicate, may need reranking.
    0.0 when only one chunk retrieved or on cache hit."""

    rrf_scores: list[float]
    """RRF scores for all retrieved chunks (pre-compression), in retrieval order."""

    # Adaptive TopK signals (FR-009, FR-015)
    query_category: Literal["short", "long", "ambiguous"]
    """Deterministic query category assigned by classify_query()."""

    top_k_used: int
    """Adaptive TopK value used for retrieval: 5, 15, or 20."""

    # Model and cost tracking (Article X, Article XII)
    model_used: str
    """LiteLLM model alias (e.g., 'economy-chat', 'premium-chat')."""

    escalation_flag: bool
    """True if a premium model was selected during generation."""

    # Compression metrics (FR-012, SC-005)
    chunks_before_compression: int
    """Number of chunks returned by hybrid_search_rrf() before compression."""

    chunks_after_compression: int
    """Number of chunks remaining after compress_context() — used to compute
    token reduction ratio for SC-005."""
