"""Why this exists: Reduces LLM costs and latency via multi-layer caching (L1/L2).
What it does: Implements SHA256 hashing (L1) and pgvector similarity search (L2).
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, select, text, true
from sqlalchemy.sql.elements import ColumnElement  # noqa: TC002

from core.config import settings
from models.schema import SemanticCache

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


def _ttl_filter() -> ColumnElement[bool]:
    """Freshness predicate: only serve entries younger than CACHE_TTL_SECONDS.

    TTL=0 disables expiry (entries live forever). No migration needed —
    reuses the existing created_at column instead of an expires_at column.
    """
    if settings.CACHE_TTL_SECONDS <= 0:
        return true()
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.CACHE_TTL_SECONDS)
    return SemanticCache.created_at > cutoff


def canonicalize_query(query: str) -> str:
    """
    Why this exists: Ensures consistent hashing for identical queries.
    What it does: Trims whitespace and converts to lowercase.
    """
    return query.strip().lower()


def generate_query_hash(query: str) -> str:
    """
    Why this exists: Provides O(1) exact match lookup key (L1).
    What it does: Returns SHA256 hex digest of the canonicalized query.
    """
    canonical = canonicalize_query(query)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def get_l1_cache(db: AsyncSession, query: str) -> dict | None:
    """
    Why this exists: Zero-cost exact match lookup.
    What it does: Queries SemanticCache by query_hash and current model.
    Returns: dict with response and citations if found.
    """
    import logfire

    from services.rag.constants import get_compatible_embed_models

    compatible_models = get_compatible_embed_models(settings.EMBED_MODEL)
    query_hash = generate_query_hash(query)
    stmt = (
        select(SemanticCache.response, SemanticCache.citations)
        .where(SemanticCache.query_hash == query_hash)
        .where(SemanticCache.model_name.in_(compatible_models))
        .where(_ttl_filter())
    )
    result = await db.execute(stmt)
    row = result.first()
    logfire.info(
        "L1 lookup: hash={h}, model={m}, found={f}",
        h=query_hash[:8],
        m=settings.EMBED_MODEL,
        f=row is not None,
    )
    if row:
        return {"response": row.response, "citations": row.citations or []}
    return None


async def get_l2_cache(
    db: AsyncSession, query_embedding: list[float], threshold: float = 0.95
) -> dict | None:
    """
    Why this exists: Semantic fallback when L1 misses.
    What it does: Performs cosine similarity search using pgvector.
    Returns: dict with response and citations if found.
    SC-003: < 5ms search target.
    """
    import logfire

    from services.rag.constants import get_compatible_embed_models

    compatible_models = get_compatible_embed_models(settings.EMBED_MODEL)
    # Using cosine similarity (1 - distance)
    # pgvector <=> operator is cosine distance
    cos_dist = SemanticCache.embedding.cosine_distance(query_embedding)
    similarity_col = (1 - cos_dist).label("similarity")
    stmt = (
        select(SemanticCache.response, SemanticCache.citations, similarity_col)
        .where(similarity_col > threshold)
        .where(SemanticCache.model_name.in_(compatible_models))
        .where(_ttl_filter())
        .order_by(text("similarity DESC"))
        .limit(1)
    )

    result = await db.execute(stmt)
    row = result.first()
    logfire.info(
        "L2 lookup: model={m}, threshold={t}, found={f}",
        m=settings.EMBED_MODEL,
        t=threshold,
        f=row is not None,
    )
    if row:
        return {
            "response": row.response,
            "citations": row.citations or [],
            "similarity": row.similarity,
        }
    return None


async def set_cache(
    db: AsyncSession,
    query: str,
    response: str,
    embedding: list[float],
    model_name: str,
    citations: list[dict] | None = None,
) -> None:
    """
    Why this exists: Persists new cache entries for future hits.
    What it does: Inserts or updates a record in the semantic_cache table,
    including citations.
    """
    import logfire

    query_hash = generate_query_hash(query)
    cache_entry = SemanticCache(
        query_hash=query_hash,
        query_text=query,
        response=response,
        embedding=embedding,
        model_name=model_name,
        citations=citations or [],
        # Explicit timestamp so upserts (merge) refresh the TTL window —
        # column default only fires on INSERT, not on merge-update.
        created_at=datetime.now(UTC),
    )
    logfire.info(
        "Cache write: hash={h}, model={m}, citations_count={c}",
        h=query_hash[:8],
        m=model_name,
        c=len(citations or []),
    )
    # Use merge to handle potential collisions (Upsert pattern)
    await db.merge(cache_entry)
    await db.commit()


async def invalidate_cache(db: AsyncSession) -> int:
    """
    Why this exists: Product data changed (ingest/re-ingest, price update) —
    cached answers may now state wrong prices/stock. TTL alone leaves a
    staleness window; this closes it immediately.
    What it does: Deletes all semantic_cache rows. Returns rows removed.
    """
    import logfire

    result = await db.execute(delete(SemanticCache))
    await db.commit()
    removed = result.rowcount or 0
    logfire.info("Semantic cache invalidated: {n} entries removed", n=removed)
    return removed
