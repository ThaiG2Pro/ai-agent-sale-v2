"""Main RAG orchestration pipeline."""

from typing import Any

import logfire
from pydantic import BaseModel

from services.ai import AIGateway
from services.rag.compression import compress_context
from services.rag.constants import (
    ANSWER_SYSTEM_PROMPT,
    CONFIDENCE_THRESHOLD,
    DECLINE_MESSAGE,
)
from services.rag.query import classify_query, compute_adaptive_topk
from services.rag.retrieval import hybrid_search_rrf
from services.semantic_cache import get_l1_cache, get_l2_cache, set_cache


class RAGResult(BaseModel):
    """Structured result from the full RAG pipeline."""

    answer: str
    declined: bool
    citations: list[dict[str, Any]]  # [{product_id, chunk_id, sku, name}]
    best_similarity: float
    rrf_scores: list[float]
    query_category: str
    top_k_used: int
    model_used: str
    escalation_flag: bool
    chunks_before_compression: int
    chunks_after_compression: int


async def answer_with_rag(
    db,
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
        else:
            logfire.info(
                "L1 cache miss: query_hash={hash}",
                hash=__import__("hashlib")
                .sha256(canonical_query.strip().lower().encode())
                .hexdigest()[:8],
            )
    except Exception as exc:
        logfire.warn("L1 cache lookup failed: {err}", err=str(exc))

    # 4. Embed query (FR-001); edge case: embedding unavailable (FR-016 #1)
    # Use original query (not LLM-normalized canonical) to ensure deterministic
    # embeddings — canonical_query is non-deterministic (LLM with temperature>0)
    # and would produce different vectors on each run for the same input.
    try:
        embeddings = await AIGateway.embed(
            input_text=query,
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
            logfire.info(
                "L2 cache hit for query: similarity={sim:.4f}",
                sim=l2_hit.get("similarity", 1.0),
            )
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
        else:
            logfire.info("L2 cache miss: no semantic match above 0.95 threshold")
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

    # 7. Hybrid retrieval with RRF (FR-005)
    retrieved = await hybrid_search_rrf(db, query_vector, fts_query_text, top_k)

    # 8. Similarity gap score (FR-010) — best cosine similarity before compression
    best_similarity = max((c["vector_score"] for c in retrieved), default=0.0)
    chunks_before = len(retrieved)
    logfire.info(
        "Retrieved {n} chunks, best_similarity={s:.4f}",
        n=chunks_before,
        s=best_similarity,
    )

    # 9. Context compression (FR-012)
    compressed = compress_context(retrieved)
    chunks_after = len(compressed)
    token_reduction = (1 - chunks_after / chunks_before) * 100 if chunks_before else 0
    logfire.info(
        "Compression: {b}->{a} chunks ({r:.0f}%% reduction)",
        b=chunks_before,
        a=chunks_after,
        r=token_reduction,
    )

    # 10. Confidence guard (FR-013) + zero-result / compression-to-empty
    #     edge cases (FR-016 #3 and #5)
    if best_similarity < CONFIDENCE_THRESHOLD or chunks_after == 0:
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

    # 11. Build context + citations (FR-011)
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
        {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Product context:\n{context}\n\nCustomer question: {query}",
        },
    ]

    # 12. LLM generation
    try:
        response = await AIGateway.complete(messages=messages, model=model)
        answer_text = response.choices[0].message.content or DECLINE_MESSAGE
    except Exception as exc:
        logfire.error("LLM generation failed: {err}", err=str(exc))
        answer_text = DECLINE_MESSAGE

    # 13. Cache write — best-effort, never blocks the response (Article X.3)
    # CRITICAL: Use EMBED_MODEL (not chat model) so cache lookups match
    try:
        from core.config import settings

        await set_cache(
            db=db,
            query=canonical_query,
            response=answer_text,
            embedding=query_vector,
            model_name=settings.EMBED_MODEL,
            citations=citations,
        )
        logfire.info("Cache write completed for embedding model")
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
