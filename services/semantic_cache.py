"""Why this exists: Reduces LLM costs and latency via multi-layer caching (L1/L2).
What it does: Implements SHA256 hashing (L1) and pgvector similarity search (L2).
"""

import hashlib
from typing import List, Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models.schema import SemanticCache


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


async def get_l1_cache(db: AsyncSession, query: str) -> Optional[str]:
    """
    Why this exists: Zero-cost exact match lookup.
    What it does: Queries SemanticCache by query_hash.
    """
    query_hash = generate_query_hash(query)
    result = await db.execute(
        select(SemanticCache.response).where(SemanticCache.query_hash == query_hash)
    )
    return result.scalar_one_or_none()


async def get_l2_cache(
    db: AsyncSession, query_embedding: List[float], threshold: float = 0.95
) -> Optional[str]:
    """
    Why this exists: Semantic fallback when L1 misses.
    What it does: Performs cosine similarity search using pgvector.
    SC-003: < 5ms search target.
    """
    # Using cosine similarity (1 - distance)
    # pgvector <=> operator is cosine distance
    cos_dist = SemanticCache.embedding.cosine_distance(query_embedding)
    similarity_col = (1 - cos_dist).label("similarity")
    stmt = (
        select(SemanticCache.response, similarity_col)
        .where(similarity_col > threshold)
        .order_by(text("similarity DESC"))
        .limit(1)
    )

    result = await db.execute(stmt)
    row = result.first()
    if row:
        return row.response
    return None


async def set_cache(
    db: AsyncSession,
    query: str,
    response: str,
    embedding: List[float],
    model_name: str,
) -> None:
    """
    Why this exists: Persists new cache entries for future hits.
    What it does: Inserts or updates a record in the semantic_cache table.
    """
    query_hash = generate_query_hash(query)
    cache_entry = SemanticCache(
        query_hash=query_hash,
        query_text=query,
        response=response,
        embedding=embedding,
        model_name=model_name,
    )
    # Use merge to handle potential collisions (Upsert pattern)
    await db.merge(cache_entry)
    await db.commit()
