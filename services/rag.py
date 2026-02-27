"""Why this exists: Core business logic for RAG (Retrieval Augmented Generation).
What it does: Implements Week 2 Vietnamese RAG pipeline — hybrid retrieval (RRF),
             adaptive TopK, context compression, confidence guard, and full RAG flow.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any, Literal

import logfire
from pydantic import BaseModel
from sqlalchemy import select, text

from core.config import settings
from models.schema import Product, TextEmbedding
from services.ai import AIGateway
from services.semantic_cache import get_l1_cache, get_l2_cache, set_cache

if TYPE_CHECKING:
    import uuid

    from sqlalchemy.ext.asyncio import AsyncSession

# ── Schema constant (mirrors models/schema.py SCHEMA) ─────────────────────────
_SCHEMA = "agent_v1"

# ── Algorithm constants (FR-005, FR-012, FR-013) ──────────────────────────────
_RRF_K: int = 60
_CONFIDENCE_THRESHOLD: float = 0.7
_COMPRESSION_SCORE_THRESHOLD: float = 0.5
_NEAR_DUP_THRESHOLD: float = 0.80

DECLINE_MESSAGE = (
    "Tôi không tìm thấy thông tin liên quan đến câu hỏi của bạn. "
    "Vui lòng thử lại với từ khóa cụ thể hơn."
)

# Action verbs used for query classification (FR-015, Q2 clarification)
_ACTION_VERBS: frozenset[str] = frozenset(
    [
        # English
        "price",
        "cost",
        "compare",
        "buy",
        "order",
        "discount",
        "ship",
        "install",
        "refund",
        "warranty",
        "available",
        "specs",
        "feature",
        "buy",
        "purchase",
        "stock",
        "quantity",
        # Vietnamese
        "giá",
        "mua",
        "đặt",
        "so sánh",
        "giao",
        "hoàn tiền",
        "cài đặt",
        "bảo hành",
        "có sẵn",
        "tính năng",
        "đặt hàng",
        "kho",
        "chiết khấu",
    ]
)

_ANSWER_SYSTEM_PROMPT = (
    "You are a helpful sales assistant for a Vietnamese SME business.\n"
    "Answer the customer's question concisely and accurately based ONLY on the "
    "provided product context.\n"
    "Respond in the same language as the customer's question.\n"
    "If the context does not fully answer the question, say so honestly."
)


# ── Pydantic output model (FR-011, FR-010) ────────────────────────────────────


class RAGResult(BaseModel):
    """Structured result from the full RAG pipeline."""

    answer: str
    declined: bool
    citations: list[dict[str, Any]]  # [{product_id, chunk_id, sku, name}]
    best_similarity: float
    rrf_scores: list[float]
    query_category: Literal["short", "long", "ambiguous"]
    top_k_used: int
    model_used: str
    escalation_flag: bool
    chunks_before_compression: int
    chunks_after_compression: int


# ── Deterministic query classification (FR-015) ───────────────────────────────


def classify_query(query: str) -> Literal["short", "long", "ambiguous"]:
    """
    Why this exists: Drives adaptive TopK for cost efficiency (FR-015).
    Rules (deterministic, word-count + keyword heuristic):
    - short:    word_count ≤ 5
    - long:     6 ≤ word_count ≤ 15
    - ambiguous: word_count > 15 AND (no action verb AND no capitalised proper noun)
    - >15 words with action verb or proper noun → "long" (safe fallback, TopK 15)
    """
    words = query.split()
    wc = len(words)

    if wc <= 5:
        return "short"
    if wc <= 15:
        return "long"

    # >15 words: check disambiguation signals
    lowered = query.lower()
    has_action_verb = any(verb in lowered for verb in _ACTION_VERBS)
    # Proper noun heuristic: any capitalised word that is NOT the first word
    has_product_name = bool(
        re.search(
            r"(?<!\A)\b[A-ZÀÁÂÃÈÉÊÌÍÒÓÔÕÙÚÝĂĐƠƯ][a-zàáâãèéêìíòóôõùúýăđơư]",
            query[query.index(" ") + 1 :] if " " in query else "",
        )
    )

    if has_action_verb or has_product_name:
        return "long"
    return "ambiguous"


def compute_adaptive_topk(query: str) -> int:
    """
    Why this exists: Maps query category to retrieval depth (FR-009).
    Returns: 5 (short) | 15 (long) | 20 (ambiguous)
    """
    return {"short": 5, "long": 15, "ambiguous": 20}[classify_query(query)]


# ── Context compression (FR-012) ──────────────────────────────────────────────


def _overlap_ratio(a: str, b: str) -> float:
    """Longest-common-subsequence string similarity ratio."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def compress_context(
    chunks: list[dict[str, Any]],
    score_threshold: float = _COMPRESSION_SCORE_THRESHOLD,
    overlap_threshold: float = _NEAR_DUP_THRESHOLD,
) -> list[dict[str, Any]]:
    """
    Why this exists: Reduces token waste before LLM call (FR-012).
    Three steps:
    1. Remove exact-text duplicates (by description).
    2. Remove chunks where vector_score < score_threshold (0.5).
    3. Remove near-duplicates (>80% overlap) — keep highest rrf_score.
    """
    if not chunks:
        return []

    # Step 1: exact dedup
    seen: set[str] = set()
    step1: list[dict[str, Any]] = []
    for chunk in chunks:
        txt = (chunk.get("description") or "").strip()
        if txt not in seen:
            seen.add(txt)
            step1.append(chunk)

    # Step 2: low-confidence filter (vector cosine similarity)
    step2 = [c for c in step1 if c.get("vector_score", 0.0) >= score_threshold]

    # Step 3: near-duplicate removal (preserve highest rrf_score)
    step2_sorted = sorted(step2, key=lambda c: c.get("rrf_score", 0.0), reverse=True)
    step3: list[dict[str, Any]] = []
    for candidate in step2_sorted:
        ctext = (candidate.get("description") or "").strip()
        is_dup = any(
            _overlap_ratio(ctext, (kept.get("description") or "").strip())
            > overlap_threshold
            for kept in step3
        )
        if not is_dup:
            step3.append(candidate)

    return step3


# ── Hybrid retrieval with Reciprocal Rank Fusion (FR-005) ─────────────────────


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
            Product.metadata_.label("metadata"),
            TextEmbedding.id.label("chunk_id"),
            vector_sim,
        )
        .join(TextEmbedding, Product.id == TextEmbedding.source_id)
        .order_by(cos_dist)
        .limit(fetch_k)
    )
    vector_rows = (await db.execute(vector_stmt)).all()

    # ── FTS search — 'simple' dictionary works for Vietnamese and English ──────
    fts_sql = text(f"""
        SELECT
            p.id::text                                                    AS product_id,
            p.sku,
            p.name,
            p.description,
            p.metadata,
            te.id::text                                                   AS chunk_id,
            ts_rank(
                to_tsvector(
                    'simple',
                    COALESCE(p.name, '') || ' ' || COALESCE(p.description, '')
                ),
                plainto_tsquery('simple', :qtext)
            )                                                             AS fts_score
        FROM {_SCHEMA}.products p
        JOIN {_SCHEMA}.text_embeddings te ON te.source_id = p.id
        WHERE to_tsvector(
                  'simple',
                  COALESCE(p.name, '') || ' ' || COALESCE(p.description, '')
              )
              @@ plainto_tsquery('simple', :qtext)
        ORDER BY fts_score DESC
        LIMIT :lim
    """)
    try:
        fts_rows = (
            await db.execute(fts_sql, {"qtext": query_text, "lim": fetch_k})
        ).all()
    except Exception as exc:
        logfire.warn(
            "FTS search failed, falling back to vector-only: {err}", err=str(exc)
        )
        fts_rows = []

    # ── RRF merge ─────────────────────────────────────────────────────────────
    scores: dict[str, float] = {}
    meta: dict[str, dict[str, Any]] = {}

    for rank, row in enumerate(vector_rows):
        cid = str(row.chunk_id)
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank)
        meta[cid] = {
            "id": str(row.product_id),
            "chunk_id": cid,
            "sku": row.sku,
            "name": row.name,
            "description": row.description,
            "metadata": dict(row.metadata) if row.metadata else {},
            "vector_score": float(row.vector_score),
            "fts_score": 0.0,
        }

    for rank, row in enumerate(fts_rows):
        cid = str(row.chunk_id)
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank)
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


# ── Full RAG pipeline (FR-007) ────────────────────────────────────────────────
# Pipeline steps:
#   1. classify  → Adaptive TopK (5/15/20) via word count + action-verb heuristic
#   2. normalize → Canonical query + keyword extraction (FR-004)
#   3. L1 cache  → SHA256 exact match (O(1), zero embedding cost)
#   4. embed     → bge-m3 via Ollama (1024 dims)
#   5. L2 cache  → pgvector cosine similarity (threshold=0.95)
#   6. truncate  → Cap FTS query at 500 words (FR-016 edge case)
#   7. hybrid    → RRF fusion (k=60) of vector + FTS results
#   8. compress  → Dedup + score<0.5 filter + >80% near-dup removal
#   9. guard     → Confidence threshold 0.7 (Article IX Section 9.3)
#  10. context   → Build prompt + extract citations
#  11. LLM       → Generate answer via LiteLLM
#  12. cache     → Write result to semantic_cache (best-effort)


async def answer_with_rag(
    db: AsyncSession,
    query: str,
    model: str = "economy-chat",
) -> RAGResult:
    """
    Why this exists: Orchestrates the complete Week 2 RAG flow (FR-007).
    Flow: classify → normalize → L1 cache → embed → L2 cache → truncate
          → hybrid_search_rrf → compress → confidence_guard → answer → cache_write.
    Edge cases per FR-016 are handled at each step.
    """
    logfire.info("RAG pipeline started: {q}", q=query[:80])

    # 1. Adaptive TopK (FR-009)
    query_category = classify_query(query)
    top_k = compute_adaptive_topk(query)

    # 2. Query normalization (FR-004) — best-effort, fall back to raw query
    canonical_query = query
    fts_query_text = query
    try:
        normalized = await AIGateway.normalize_query(query)
        canonical_query = normalized.canonical
        if normalized.extracted_keywords:
            fts_query_text = " ".join(normalized.extracted_keywords)
        logfire.info(
            "Query normalized: lang={lang}, intent={intent}",
            lang=normalized.detected_language,
            intent=normalized.intent,
        )
    except Exception as exc:
        logfire.warn(
            "normalize_query failed, using raw query: {err}",
            err=str(exc),
        )

    # 3. L1 cache check — exact match, zero-cost (Article X.3)
    try:
        l1_hit = await get_l1_cache(db, canonical_query)
        if l1_hit is not None:
            logfire.info("L1 cache hit for query")
            return RAGResult(
                answer=l1_hit["response"],
                declined=False,
                citations=l1_hit["citations"],
                best_similarity=1.0,
                rrf_scores=[],
                query_category=query_category,
                top_k_used=top_k,
                model_used="cache",
                escalation_flag=False,
                chunks_before_compression=0,
                chunks_after_compression=0,
            )
    except Exception as exc:
        logfire.warn("L1 cache lookup failed: {err}", err=str(exc))

    # 4. Embed query (FR-001); edge case: embedding unavailable (FR-016 #1)
    try:
        embeddings = await AIGateway.embed(
            input_text=canonical_query,
            model="economy-embedding",
        )
        query_vector = embeddings[0]
    except Exception as exc:
        logfire.error("Embedding service unavailable: {err}", err=str(exc))
        return RAGResult(
            answer="Dịch vụ tạm thời không khả dụng. Vui lòng thử lại sau.",
            declined=True,
            citations=[],
            best_similarity=0.0,
            rrf_scores=[],
            query_category=query_category,
            top_k_used=top_k,
            model_used=model,
            escalation_flag=False,
            chunks_before_compression=0,
            chunks_after_compression=0,
        )

    # 5. L2 cache check — semantic match (Article X.3)
    try:
        l2_hit = await get_l2_cache(db, query_vector, threshold=0.95)
        if l2_hit is not None:
            logfire.info("L2 cache hit for query")
            return RAGResult(
                answer=l2_hit["response"],
                declined=False,
                citations=l2_hit["citations"],
                best_similarity=1.0,
                rrf_scores=[],
                query_category=query_category,
                top_k_used=top_k,
                model_used="cache",
                escalation_flag=False,
                chunks_before_compression=0,
                chunks_after_compression=0,
            )
    except Exception as exc:
        logfire.warn("L2 cache lookup failed: {err}", err=str(exc))

    # 6. Truncate >500-word queries (FR-016 #4)
    fts_words = fts_query_text.split()
    if len(fts_words) > 500:
        fts_query_text = " ".join(fts_words[:500])
        logfire.info(
            "FTS query truncated: {orig} -> 500 words",
            orig=len(fts_words),
        )

    # 5. Hybrid retrieval with RRF (FR-005)
    retrieved = await hybrid_search_rrf(db, query_vector, fts_query_text, top_k)

    # 6. Similarity gap score (FR-010) — best cosine similarity before compression
    best_similarity = max((c["vector_score"] for c in retrieved), default=0.0)
    chunks_before = len(retrieved)
    logfire.info(
        "Retrieved {n} chunks, best_similarity={s:.4f}",
        n=chunks_before,
        s=best_similarity,
    )

    # 7. Context compression (FR-012)
    compressed = compress_context(retrieved)
    chunks_after = len(compressed)
    token_reduction = (1 - chunks_after / chunks_before) * 100 if chunks_before else 0
    logfire.info(
        "Compression: {b}->{a} chunks ({r:.0f}%% reduction)",
        b=chunks_before,
        a=chunks_after,
        r=token_reduction,
    )

    # 8. Confidence guard (FR-013) + zero-result / compression-to-empty
    #    edge cases (FR-016 #3 and #5)
    if best_similarity < _CONFIDENCE_THRESHOLD or chunks_after == 0:
        logfire.info(
            "Confidence guard fired: best_sim={s:.4f}, chunks_after={n}",
            s=best_similarity,
            n=chunks_after,
        )
        return RAGResult(
            answer=DECLINE_MESSAGE,
            declined=True,
            citations=[],
            best_similarity=best_similarity,
            rrf_scores=[c["rrf_score"] for c in retrieved],
            query_category=query_category,
            top_k_used=top_k,
            model_used=model,
            escalation_flag=False,
            chunks_before_compression=chunks_before,
            chunks_after_compression=chunks_after,
        )

    # 9. Build context + citations (FR-011)
    context_parts: list[str] = []
    citations: list[dict[str, Any]] = []
    for chunk in compressed:
        context_parts.append(
            f"[{chunk['sku']}] {chunk['name']}:\n{chunk['description']}"
        )
        citations.append(
            {
                "product_id": chunk["id"],
                "chunk_id": chunk["chunk_id"],
                "sku": chunk["sku"],
                "name": chunk["name"],
            }
        )

    context = "\n\n".join(context_parts)
    messages = [
        {"role": "system", "content": _ANSWER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Product context:\n{context}\n\nCustomer question: {query}",
        },
    ]

    # 10. LLM generation
    try:
        response = await AIGateway.complete(messages=messages, model=model)
        answer_text = response.choices[0].message.content or DECLINE_MESSAGE
    except Exception as exc:
        logfire.error("LLM generation failed: {err}", err=str(exc))
        answer_text = DECLINE_MESSAGE

    # 11. Cache write — best-effort, never blocks the response (Article X.3)
    try:
        await set_cache(
            db=db,
            query=canonical_query,
            response=answer_text,
            embedding=query_vector,
            model_name=model,
            citations=citations,
        )
    except Exception as exc:
        logfire.warn("Cache write failed: {err}", err=str(exc))

    return RAGResult(
        answer=answer_text,
        declined=False,
        citations=citations,
        best_similarity=best_similarity,
        rrf_scores=[c["rrf_score"] for c in retrieved],
        query_category=query_category,
        top_k_used=top_k,
        model_used=model,
        escalation_flag=False,
        chunks_before_compression=chunks_before,
        chunks_after_compression=chunks_after,
    )


# ── Backward-compatible API (used by existing integration tests & CLI) ─────────


async def ingest_product_text(
    db: AsyncSession,
    name: str,
    sku: str,
    description: str,
    price: float = 0.0,
    metadata: dict[str, Any] | None = None,
) -> uuid.UUID:
    """
    Why this exists: Populates the system with searchable product knowledge.
    What it does: Creates a Product record and stores its embedding with
                  governance fields.
    """
    product = Product(
        name=name,
        sku=sku,
        description=description,
        price=price,
        metadata_=metadata or {},
    )
    db.add(product)
    await db.flush()

    logfire.info("Generating embedding for product: {sku}", sku=sku)
    embeddings = await AIGateway.embed(
        input_text=description, model="economy-embedding"
    )
    vector = embeddings[0]

    # Keyword extraction (T017b)
    try:
        keywords = await AIGateway.extract_keywords(description, count=5)
    except Exception as exc:
        logfire.warn("Keyword extraction failed: {err}", err=str(exc))
        keywords = []

    embedding_record = TextEmbedding(
        source_id=product.id,
        source_type="product_description",
        embedding=vector,
        model_name=settings.EMBED_MODEL,
        model_version="v1.0",
        keywords=keywords,
    )
    db.add(embedding_record)
    await db.commit()

    return product.id


async def search_products(
    db: AsyncSession, query: str, top_k: int = 5
) -> list[dict[str, Any]]:
    """
    Why this exists: Semantic retrieval for the AI agent and backward compatibility.
    What it does: Embeds the query and searches pgvector. Returns normalised score dict.
    SC-002 Target: p95 ≤ 5s end-to-end.
    """
    logfire.info("Vector search: {query}", query=query)
    embeddings = await AIGateway.embed(input_text=query, model="economy-embedding")
    query_vector = embeddings[0]

    cos_dist = TextEmbedding.embedding.cosine_distance(query_vector)
    similarity = (1 - cos_dist).label("similarity")
    stmt = (
        select(
            Product.id.label("product_id"),
            Product.sku,
            Product.name,
            Product.description,
            Product.metadata_.label("metadata"),
            TextEmbedding.id.label("chunk_id"),
            similarity,
        )
        .join(TextEmbedding, Product.id == TextEmbedding.source_id)
        .order_by(cos_dist)
        .limit(top_k)
    )

    result = await db.execute(stmt)
    return [
        {
            "id": str(row.product_id),
            "chunk_id": str(row.chunk_id),
            "sku": row.sku,
            "name": row.name,
            "description": row.description,
            "score": float(row.similarity),
            "metadata": dict(row.metadata) if row.metadata else {},
        }
        for row in result.all()
    ]
