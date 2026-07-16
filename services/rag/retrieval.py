"""Hybrid retrieval with Reciprocal Rank Fusion (RRF) and timeout protection."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import logfire
from sqlalchemy import select, text

from core.config import settings
from models.schema import Product, TextEmbedding
from services.rag.constants import RRF_K, SCHEMA

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def hybrid_search_rrf(
    db: AsyncSession,
    query_vector: list[float],
    query_text: str,
    top_k: int,
) -> list[dict[str, Any]]:
    """
    Why this exists: Higher recall than vector-only by combining semantic +
    keyword signals.
    Algorithm: RRF — final_score = 1/(k+rank_vector) + 1/(k+rank_fts), k=60.
    Chunks absent from one source receive max_rank+1 as their rank penalty.
    """
    fetch_k = top_k * 2  # Over-fetch to have merge headroom

    # ── Vector search ─────────────────────────────────────────────────────────
    cos_dist = TextEmbedding.embedding.cosine_distance(query_vector)
    vector_sim = (1 - cos_dist).label("vector_score")
    vector_stmt = (
        select(
            Product.id.label("product_id"),
            Product.sku,
            Product.name,
            Product.description,
            Product.price,
            Product.metadata_.label("metadata"),
            TextEmbedding.id.label("chunk_id"),
            vector_sim,
        )
        .join(TextEmbedding, Product.id == TextEmbedding.source_id)
        .where(TextEmbedding.model_name == settings.EMBED_MODEL)
        .order_by(cos_dist)
        .limit(fetch_k)
    )
    try:
        result = await asyncio.wait_for(db.execute(vector_stmt), timeout=10.0)
        vector_rows = result.all()
    except TimeoutError:
        logfire.error("Vector search timed out (junk/complex query)")
        return []

    # ── FTS search — stored content_tsvector column with unaccent + setweight ──
    # content_tsvector is a GENERATED ALWAYS AS stored column:
    #   setweight(to_tsvector('simple', immutable_unaccent(name)), 'A') ||
    #   setweight(to_tsvector('simple', immutable_unaccent(description)), 'B')
    # immutable_unaccent strips diacritics so Vietnamese users can search
    # without accents ("dien thoai" matches "điện thoại").
    fts_sql = text(f"""
        SELECT
            p.id::text                                                    AS product_id,
            p.sku,
            p.name,
            p.description,
            p.price,
            p.metadata,
            te.id::text                                                   AS chunk_id,
            ts_rank(
                p.content_tsvector,
                plainto_tsquery('simple', {SCHEMA}.immutable_unaccent(:qtext))
            )                                                             AS fts_score
        FROM {SCHEMA}.products p
        JOIN {SCHEMA}.text_embeddings te ON te.source_id = p.id
        WHERE p.content_tsvector
              @@ plainto_tsquery('simple', {SCHEMA}.immutable_unaccent(:qtext))
        ORDER BY fts_score DESC
        LIMIT :lim
    """)
    try:
        result = await asyncio.wait_for(
            db.execute(fts_sql, {"qtext": query_text, "lim": fetch_k}), timeout=10.0
        )
        fts_rows = result.all()
    except TimeoutError:
        logfire.warn("FTS search timed out (complex query)")
        fts_rows = []
    except Exception as exc:
        logfire.warn("FTS search failed, falling back to vector-only: {err}", err=str(exc))
        await db.rollback()
        fts_rows = []

    # ── RRF merge ─────────────────────────────────────────────────────────────
    scores: dict[str, float] = {}
    meta: dict[str, dict[str, Any]] = {}

    for rank, row in enumerate(vector_rows):
        cid = str(row.chunk_id)
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank)
        meta[cid] = {
            "id": str(row.product_id),
            "chunk_id": cid,
            "sku": row.sku,
            "name": row.name,
            "description": row.description,
            "price": float(row.price) if row.price is not None else None,
            "metadata": dict(row.metadata) if row.metadata else {},
            "vector_score": float(row.vector_score),
            "fts_score": 0.0,
        }

    for rank, row in enumerate(fts_rows):
        cid = str(row.chunk_id)
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank)
        if cid in meta:
            meta[cid]["fts_score"] = float(row.fts_score)
        else:
            # FTS-only hit: no vector match, so vector_score = 0.0
            meta[cid] = {
                "id": str(row.product_id),
                "chunk_id": cid,
                "sku": row.sku,
                "name": row.name,
                "description": row.description,
                "price": None,
                "metadata": dict(row.metadata) if row.metadata else {},
                "vector_score": 0.0,
                "fts_score": float(row.fts_score),
            }

    merged: list[dict[str, Any]] = []
    for cid, rrf in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        chunk = dict(meta[cid])
        chunk["rrf_score"] = rrf
        merged.append(chunk)

    return merged[:top_k]


async def search_products(
    db: AsyncSession,
    query: str,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """
    Entry point for semantic product search.
    Accepts plain query text, embeds it, then runs hybrid RRF.
    """
    from services.ai import AIGateway

    try:
        # Embed the query
        embeddings = await AIGateway.embed(input_text=query, model="economy-embedding")
        query_vector = embeddings[0]
        # Run hybrid search with timeout
        results = await asyncio.wait_for(
            hybrid_search_rrf(db, query_vector, query, top_k), timeout=10.0
        )
        # Normalize results: add 'score' key for compatibility with search CLI
        for result in results:
            result["score"] = result.get("rrf_score", 0.0)
        return results
    except TimeoutError:
        logfire.error("search_products timed out")
        return []
    except Exception as exc:
        logfire.error("search_products failed: {err}", err=str(exc))
        return []
