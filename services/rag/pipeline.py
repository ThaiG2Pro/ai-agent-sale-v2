"""Main RAG orchestration pipeline."""

import time
from typing import Any

import logfire
from pydantic import BaseModel
from uuid_utils import uuid7

from models.schema import ModelTrace
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
    similarity_gap: float  # Score_top1 - Score_top2; 0.0 on cache hit / single result
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

    # 1. Adaptive TopK (FR-009) — initial estimate by word count;
    #    overridden after normalization using intent (more precise)
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
            "Query normalized: lang={lang}, intent={intent}, is_valid={v}",
            lang=normalized.detected_language,
            intent=normalized.intent,
            v=normalized.is_valid,
        )
        # 2a. is_valid guard — reject spam/gibberish before any DB/LLM calls
        if not normalized.is_valid:
            logfire.info("Query rejected by is_valid guard (spam/gibberish)")
            return RAGResult(
                answer="Vui lòng đặt câu hỏi liên quan đến sản phẩm hoặc dịch vụ.",
                declined=True,
                citations=[],
                best_similarity=0.0,
                similarity_gap=0.0,
                rrf_scores=[],
                query_category=query_category,
                top_k_used=top_k,
                model_used="guard",
                escalation_flag=False,
                chunks_before_compression=0,
                chunks_after_compression=0,
            )
        # 2b. Intent-driven TopK override (FR-009 + FR-015)
        #     Normalization knows the intent precisely; use it to refine TopK
        #     set in step 1. Focused intents need far fewer chunks.
        top_k = compute_adaptive_topk(query, intent=normalized.intent)
        logfire.info(
            "TopK adjusted by intent: intent={i}, top_k={k}",
            i=normalized.intent,
            k=top_k,
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
                similarity_gap=0.0,
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
            similarity_gap=0.0,
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
                similarity_gap=0.0,
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

    # 8. Similarity gap score (FR-010)
    #    best_similarity: top-1 cosine score used for confidence guard
    #    similarity_gap: top1 - top2 measures retrieval confidence
    #      large gap → clear winner, small gap → ambiguous (may need reranking)
    vec_scores = sorted((c["vector_score"] for c in retrieved), reverse=True)
    best_similarity = vec_scores[0] if vec_scores else 0.0
    similarity_gap = (
        vec_scores[0] - vec_scores[1] if len(vec_scores) >= 2 else best_similarity
    )
    chunks_before = len(retrieved)
    logfire.info(
        "Retrieved {n} chunks, best_similarity={s:.4f}, similarity_gap={g:.4f}",
        n=chunks_before,
        s=best_similarity,
        g=similarity_gap,
    )

    # 9. Context compression (FR-012)
    compressed = compress_context(retrieved, best_similarity=best_similarity)
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
    guard_decision = "ACCEPTED"
    if best_similarity < CONFIDENCE_THRESHOLD or chunks_after == 0:
        guard_decision = "REJECTED"
        logfire.info(
            "Confidence guard fired: best_sim={s:.4f}, chunks_after={n}",
            s=best_similarity,
            n=chunks_after,
        )
        # Write model_trace for rejected queries (FR-010 observability)
        await _write_model_trace(
            db=db,
            model_name=model,
            guard_decision=guard_decision,
            best_similarity=best_similarity,
            similarity_gap=similarity_gap,
            top_k_used=top_k,
            query_category=query_category,
        )
        return RAGResult(
            answer=DECLINE_MESSAGE,
            declined=True,
            citations=[],
            best_similarity=best_similarity,
            similarity_gap=similarity_gap,
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
        price = chunk.get("price")
        price_line = f"Giá: {price:,.0f} VND" if price is not None else "Giá: Liên hệ"
        context_parts.append(
            f"[{chunk['sku']}] {chunk['name']}\n{price_line}\n{chunk['description']}"
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

    # 12. LLM generation — timed for model_trace latency
    gen_start = time.perf_counter()
    llm_response = None
    try:
        llm_response = await AIGateway.complete(messages=messages, model=model)
        answer_text = llm_response.choices[0].message.content or DECLINE_MESSAGE
    except Exception as exc:
        logfire.error("LLM generation failed: {err}", err=str(exc))
        answer_text = DECLINE_MESSAGE
    gen_latency_ms = (time.perf_counter() - gen_start) * 1000

    # 13. model_trace write — best-effort, captures gap + guard + cost (FR-010)
    await _write_model_trace(
        db=db,
        model_name=model,
        guard_decision=guard_decision,
        best_similarity=best_similarity,
        similarity_gap=similarity_gap,
        top_k_used=top_k,
        query_category=query_category,
        llm_response=llm_response,
        latency_ms=gen_latency_ms,
    )

    # 14. Cache write — best-effort, never blocks the response (Article X.3)
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
        similarity_gap=similarity_gap,
        rrf_scores=[c["rrf_score"] for c in retrieved],
        query_category=query_category,
        top_k_used=top_k,
        model_used=model,
        escalation_flag=False,
        chunks_before_compression=chunks_before,
        chunks_after_compression=chunks_after,
    )


async def _write_model_trace(
    db,
    model_name: str,
    guard_decision: str,
    best_similarity: float,
    similarity_gap: float,
    top_k_used: int,
    query_category: str,
    llm_response: Any = None,
    latency_ms: float = 0.0,
) -> None:
    """
    Why this exists: Persists retrieval quality signals to model_traces (FR-010).
    What it does: Writes similarity_gap, guard_decision, token counts, and cost
                  so that offline analysis can tune thresholds and models.
    Best-effort — never raises, never blocks the response.
    """
    try:
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        cost = 0.0

        if llm_response is not None:
            usage = getattr(llm_response, "usage", None)
            if usage:
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(usage, "completion_tokens", 0) or 0
                total_tokens = getattr(usage, "total_tokens", 0) or 0
            try:
                import litellm

                cost = litellm.completion_cost(completion_response=llm_response) or 0.0
            except Exception:
                pass

        from decimal import Decimal

        trace = ModelTrace(
            id=uuid7(),
            message_id=None,  # Week 5: link to ConversationMessage
            model_name=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            cost=Decimal(str(round(cost, 6))),
            metadata_={
                "guard_decision": guard_decision,
                "best_similarity": round(best_similarity, 4),
                "similarity_gap": round(similarity_gap, 4),
                "top_k_used": top_k_used,
                "query_category": query_category,
            },
        )
        db.add(trace)
        await db.flush()
        logfire.info(
            "model_trace written: guard={g}, gap={gap:.4f}, tokens={t}",
            g=guard_decision,
            gap=similarity_gap,
            t=total_tokens,
        )
    except Exception as exc:
        logfire.warn("model_trace write failed (non-blocking): {err}", err=str(exc))
