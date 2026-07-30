"""Main RAG orchestration pipeline."""

import time
from typing import Any

import logfire
from pydantic import BaseModel, Field
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
    similarity_gap: float = 0.0  # Score_top1 - Score_top2; 0.0 on cache hit / single result
    rrf_scores: list[float]
    query_category: str
    top_k_used: int
    model_used: str
    escalation_flag: bool
    chunks_before_compression: int
    chunks_after_compression: int


class RetrievalResult(BaseModel):
    """Result from search_and_retrieve() — retrieval only, no LLM generation.

    Why: Separates retrieval from answer generation so that:
    - Declined queries (Layer 1/2) never waste an LLM call
    - Cache hits return pre-generated answer without LLM
    - Escalated queries use the correct (premium) model for answer generation
    """

    cached_answer: str | None  # Pre-generated answer if L1/L2 cache hit
    cached_citations: list[dict[str, Any]]  # Citations from cache hit (empty if no hit)
    declined: bool  # True if Layer 1 guard fired (sim < CONFIDENCE_THRESHOLD)
    citations: list[dict[str, Any]]  # [{product_id, chunk_id, sku, name, source_text}]
    chunks: list[dict[str, Any]]  # Compressed chunks for answer generation in answer_node
    best_similarity: float
    similarity_gap: float = 0.0
    canonical_query: str  # Normalized query for L1 cache write
    query_vector: list[float]  # Embedded vector for L2 cache write
    query_category: str
    top_k_used: int
    # ── Additive observability fields (agentic-rag-retry-loop, ticket 2026 — Design
    # Deviation D1, orchestrator-approved). Safe zero-defaults so any existing caller that
    # does not read these fields sees no behavior change. Lets retrieve_with_retry/
    # answer_with_rag report the same pre-compression chunk count + RRF scores that
    # RAGResult already exposed, without introducing a second retrieval codepath. ──
    chunks_before_compression: int = 0
    chunks_after_compression: int = 0
    rrf_scores: list[float] = Field(default_factory=list)
    # Design Deviation D2 (orchestrator-approved): distinguishes WHY declined=True so
    # answer_with_rag can reproduce the exact pre-existing decline text + model_used per
    # reason at kill-switch=0 (RISK-005 byte-identical guarantee). None when not declined.
    decline_reason: str | None = None  # "spam" | "embed_unavailable" | "layer1_guard" | None


async def search_and_retrieve(db, query: str, intent: str | None = None) -> RetrievalResult:
    """Retrieval-only pipeline: classify → normalize → cache → embed → search → compress.

    Does NOT call LLM for answer generation. Returns RetrievalResult with:
    - cached_answer: if L1/L2 cache hit (answer_node returns this directly)
    - chunks + citations: for answer generation in answer_node
    - declined=True: if Layer 1 guard fired (sim < 0.45)
    - canonical_query + query_vector: for cache write in answer_node

    Why separated from answer_with_rag: confidence_node may decline after retrieval.
    Calling LLM in retrieval_node wastes tokens for declined queries and doubles
    cost for accepted queries (answer_node also calls LLM).
    """
    logfire.info("RAG pipeline started: {q}", q=query[:80])

    query_category = classify_query(query)
    top_k = compute_adaptive_topk(query)

    # Normalize query (best-effort) — skip if intent is pre-classified
    canonical_query = query
    fts_query_text = query

    if intent is None:
        # If intent not provided, run full normalization (includes LLM call)
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
            if not normalized.is_valid:
                logfire.info("Query rejected by is_valid guard (spam/gibberish)")
                return RetrievalResult(
                    cached_answer=None,
                    cached_citations=[],
                    declined=True,
                    citations=[],
                    chunks=[],
                    best_similarity=0.0,
                    similarity_gap=0.0,
                    canonical_query=canonical_query,
                    query_vector=[],
                    query_category=query_category,
                    top_k_used=top_k,
                    decline_reason="spam",
                )
            top_k = compute_adaptive_topk(query, intent=normalized.intent)
            logfire.info(
                "TopK adjusted by intent: intent={i}, top_k={k}",
                i=normalized.intent,
                k=top_k,
            )
        except Exception as exc:
            logfire.warn("normalize_query failed, using raw query: {err}", err=str(exc))
    else:
        # Intent pre-classified from router_node — skip expensive LLM call
        top_k = compute_adaptive_topk(query, intent=intent)
        logfire.info(
            "TopK adjusted by pre-classified intent: intent={i}, top_k={k}",
            i=intent,
            k=top_k,
        )

    # L1 cache check
    try:
        l1_hit = await get_l1_cache(db, canonical_query)
        if l1_hit is not None:
            logfire.info("L1 cache hit for query")
            return RetrievalResult(
                cached_answer=l1_hit["response"],
                cached_citations=l1_hit.get("citations", []),
                declined=False,
                citations=l1_hit.get("citations", []),
                chunks=[],
                best_similarity=1.0,
                similarity_gap=0.0,
                canonical_query=canonical_query,
                query_vector=[],  # no vector needed for L1 hit (already cached)
                query_category=query_category,
                top_k_used=top_k,
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

    # Embed query
    try:
        embeddings = await AIGateway.embed(input_text=query, model="economy-embedding")
        query_vector = embeddings[0]
    except Exception as exc:
        logfire.error("Embedding service unavailable: {err}", err=str(exc))
        return RetrievalResult(
            cached_answer=None,
            cached_citations=[],
            declined=True,
            citations=[],
            chunks=[],
            best_similarity=0.0,
            similarity_gap=0.0,
            canonical_query=canonical_query,
            query_vector=[],
            query_category=query_category,
            top_k_used=top_k,
            decline_reason="embed_unavailable",
        )

    # L2 cache check
    try:
        l2_hit = await get_l2_cache(db, query_vector, threshold=0.95)
        if l2_hit is not None:
            logfire.info(
                "L2 cache hit for query: similarity={sim:.4f}", sim=l2_hit.get("similarity", 1.0)
            )
            return RetrievalResult(
                cached_answer=l2_hit["response"],
                cached_citations=l2_hit.get("citations", []),
                declined=False,
                citations=l2_hit.get("citations", []),
                chunks=[],
                best_similarity=1.0,
                similarity_gap=0.0,
                canonical_query=canonical_query,
                query_vector=query_vector,
                query_category=query_category,
                top_k_used=top_k,
            )
        else:
            logfire.info("L2 cache miss: no semantic match above 0.95 threshold")
    except Exception as exc:
        logfire.warn("L2 cache lookup failed: {err}", err=str(exc))

    # FTS truncation
    fts_words = fts_query_text.split()
    if len(fts_words) > 500:
        fts_query_text = " ".join(fts_words[:500])

    # Hybrid retrieval
    retrieved = await hybrid_search_rrf(db, query_vector, fts_query_text, top_k)

    # Similarity scores
    vec_scores = sorted((c["vector_score"] for c in retrieved), reverse=True)
    best_similarity = vec_scores[0] if vec_scores else 0.0
    similarity_gap = vec_scores[0] - vec_scores[1] if len(vec_scores) >= 2 else best_similarity
    chunks_before = len(retrieved)
    logfire.info(
        "Retrieved {n} chunks, best_similarity={s:.4f}, similarity_gap={g:.4f}",
        n=chunks_before,
        s=best_similarity,
        g=similarity_gap,
    )

    # Context compression
    compressed = compress_context(retrieved, best_similarity=best_similarity)
    chunks_after = len(compressed)
    token_reduction = (1 - chunks_after / chunks_before) * 100 if chunks_before else 0
    logfire.info(
        "Compression: {b}->{a} chunks ({r:.0f}%% reduction)",
        b=chunks_before,
        a=chunks_after,
        r=token_reduction,
    )

    # Layer 1 guard (FR-013)
    if best_similarity < CONFIDENCE_THRESHOLD or chunks_after == 0:
        logfire.info(
            "Layer 1 guard fired: best_sim={s:.4f}, chunks_after={n}",
            s=best_similarity,
            n=chunks_after,
        )
        return RetrievalResult(
            cached_answer=None,
            cached_citations=[],
            declined=True,
            citations=[],
            chunks=[],
            best_similarity=best_similarity,
            similarity_gap=similarity_gap,
            canonical_query=canonical_query,
            query_vector=query_vector,
            query_category=query_category,
            top_k_used=top_k,
            chunks_before_compression=chunks_before,
            chunks_after_compression=chunks_after,
            rrf_scores=[c["rrf_score"] for c in retrieved],
            decline_reason="layer1_guard",
        )

    # Build citations from compressed chunks
    citations: list[dict[str, Any]] = []
    for chunk in compressed:
        price = chunk.get("price")
        price_line = f"Giá: {price:,.0f} VND" if price is not None else "Giá: Liên hệ"
        source_text = f"[{chunk['sku']}] {chunk['name']}\n{price_line}\n{chunk['description']}"
        citations.append(
            {
                "product_id": chunk["id"],
                "chunk_id": chunk["chunk_id"],
                "sku": chunk["sku"],
                "name": chunk["name"],
                "source_text": source_text,
            }
        )

    return RetrievalResult(
        cached_answer=None,
        cached_citations=[],
        declined=False,
        citations=citations,
        chunks=compressed,
        best_similarity=best_similarity,
        similarity_gap=similarity_gap,
        canonical_query=canonical_query,
        query_vector=query_vector,
        query_category=query_category,
        top_k_used=top_k,
        chunks_before_compression=chunks_before,
        chunks_after_compression=chunks_after,
        rrf_scores=[c["rrf_score"] for c in retrieved],
    )


async def retrieve_with_retry(
    db,
    query: str,
    intent: str | None = None,
) -> RetrievalResult:
    """
    Why this exists: Bounded self-evaluate -> rewrite -> re-retrieve loop that recovers
    answerable queries a single retrieval pass phrased poorly (agentic-rag-retry-loop,
    ticket 2026, design.md §Control Flow). Reuses search_and_retrieve's own Layer-1 signals
    (ADR-002) — introduces NO new numeric scorer.
    What it does: Runs attempt 0 via search_and_retrieve. Returns immediately (zero added
    cost) for COMPARISON intent (ADR-004 — the existing split fallback handles recovery),
    a sufficient result or cache hit, a spam/embed-unavailable decline, or when the retry
    budget is 0 (kill switch — AC-2026-014, byte-identical to the pre-2026 static pipeline,
    RISK-005). Otherwise rewrites the query on the light tier (AIGateway.rewrite_query) and
    re-retrieves up to settings.RAG_RETRY_MAX_ATTEMPTS times (bounded for-loop — ADR-003,
    RISK-001), stopping early on no-progress (identical/empty rewrite, subject drift, no
    similarity gain, identical top chunk_ids) or on any mid-loop failure. Returns the
    accepted result, or the best-seen declined result on exhaustion/failure — never raises.
    """
    from core.config import settings

    max_attempts = settings.RAG_RETRY_MAX_ATTEMPTS
    result = await search_and_retrieve(db, query, intent)

    if intent == "COMPARISON":
        # ADR-004: mutual exclusion — the split fallback in retrieval_node handles
        # COMPARISON recovery; looping here too would double the retrieval storm
        # (AC-2026-020, INT-2026-006).
        return result
    if not result.declined:
        # Sufficient first pass or L1/L2 cache hit — no loop (AC-2026-001, AC-2026-019).
        return result
    if not result.query_vector:
        # Spam (`is_valid=false`) or embedding-unavailable — bypass the loop, never
        # rewrite spam / retry a dead embed service (AC-2026-022, AC-2026-011, ADR-002).
        return result
    if max_attempts == 0:
        # Kill switch — exact static single-pass behavior (AC-2026-014, RISK-005).
        return result

    best = result
    current_query = result.canonical_query.strip()
    prev_chunk_ids = {c["chunk_id"] for c in result.citations}  # empty on Layer-1 decline

    for attempt in range(1, max_attempts + 1):  # bounded for-loop — RISK-001 (ADR-003)
        try:
            rewritten = await AIGateway.rewrite_query(current_query)
        except Exception as exc:
            logfire.warn(
                "retrieve_with_retry: rewrite_query raised, aborting to best-seen: {err}",
                err=str(exc),
            )
            break  # AC-2026-011, AC-2026-018 — abort to best-seen, no partial state

        new_query = rewritten.query.strip()
        if (
            not new_query
            or not rewritten.keeps_subject
            or new_query.lower() == current_query.lower()
        ):
            logfire.info(
                "retrieve_with_retry: no-progress/subject-drift at attempt {a} — stopping",
                a=attempt,
            )
            break  # AC-2026-012, AC-2026-015; BR-2026-004 / BR-2026-005

        await _write_retry_trace(
            db=db,
            attempt=attempt,
            rewritten_query=new_query,
            guard_decision="RETRY",
            best_similarity=best.best_similarity,
            query_category=result.query_category,
        )

        try:
            result = await search_and_retrieve(db, new_query, intent)
        except Exception as exc:
            logfire.warn(
                "retrieve_with_retry: search_and_retrieve raised, aborting to best-seen: {err}",
                err=str(exc),
            )
            break  # AC-2026-018 — mid-loop abort, no partial/corrupt state

        if not result.declined:
            return result  # rewrite recovered the query (AC-2026-009)

        new_chunk_ids = {c["chunk_id"] for c in result.citations}
        if result.best_similarity <= best.best_similarity or new_chunk_ids == prev_chunk_ids:
            # No-progress: similarity did not improve, or the re-retrieval surfaced the
            # same top chunk_ids as before (AC-2026-015; BR-2026-005). NOTE (documented for
            # QA — see tests/unit/test_retrieve_with_retry.py): when two consecutive
            # attempts BOTH decline with zero surviving chunks, prev_chunk_ids and
            # new_chunk_ids are both the empty set and therefore compare equal — the loop
            # stops on "same chunk_ids" even though it is really "still zero chunks", so at
            # cap=2 a second attempt's similarity improvement is never applied if chunks
            # stay empty across both attempts (AC-2026-015 interaction).
            break

        best = result
        current_query, prev_chunk_ids = new_query, new_chunk_ids

    return best  # exhausted or stopped early, still insufficient (AC-2026-016)


async def answer_with_rag(
    db,
    query: str,
    model: str = "economy-chat",
) -> RAGResult:
    """
    Why this exists: Orchestrates the complete RAG flow (FR-007). Retrieval (classify →
    normalize → L1/L2 cache → embed → hybrid RRF → compress → Layer-1 guard → [rewrite →
    re-retrieve]*N) is delegated to `retrieve_with_retry` (ADR-001, agentic-rag-retry-loop,
    ticket 2026) so `/query` + the CLI get the same bounded retry as the LangGraph path —
    this also removes the retrieval logic this function used to duplicate inline (G3).
    Generation, model_trace, and cache-write stay here; only the FINAL accepted result is
    ever cached (AC-2026-023).

    Design Deviation D2 (orchestrator-approved): `RetrievalResult.decline_reason` lets this
    function reproduce the EXACT pre-existing decline text + `model_used` per reason (spam /
    embed-unavailable / Layer-1 guard), so `RAG_RETRY_MAX_ATTEMPTS=0` stays byte-identical to
    the pre-change single-pass behavior (RISK-005).
    """
    logfire.info("RAG pipeline started: {q}", q=query[:80])

    # answer_with_rag never pre-classifies intent — intent=None reproduces the original
    # always-normalize behavior exactly (search_and_retrieve runs the full normalize_query
    # + is_valid guard when intent is None).
    result = await retrieve_with_retry(db, query, intent=None)

    # ── Cache hit (L1 or L2) — return the pre-generated answer directly, no LLM call ──────
    if result.cached_answer is not None:
        return RAGResult(
            answer=result.cached_answer,
            declined=False,
            citations=result.cached_citations,
            best_similarity=result.best_similarity,
            similarity_gap=result.similarity_gap,
            rrf_scores=result.rrf_scores,
            query_category=result.query_category,
            top_k_used=result.top_k_used,
            model_used="cache",
            escalation_flag=False,
            chunks_before_compression=result.chunks_before_compression,
            chunks_after_compression=result.chunks_after_compression,
        )

    # ── Declined — reproduce the exact pre-existing message/model_used per reason ─────────
    if result.declined:
        if result.decline_reason == "spam":
            return RAGResult(
                answer="Vui lòng đặt câu hỏi liên quan đến sản phẩm hoặc dịch vụ.",
                declined=True,
                citations=[],
                best_similarity=0.0,
                similarity_gap=0.0,
                rrf_scores=[],
                query_category=result.query_category,
                top_k_used=result.top_k_used,
                model_used="guard",
                escalation_flag=False,
                chunks_before_compression=0,
                chunks_after_compression=0,
            )
        if result.decline_reason == "embed_unavailable":
            return RAGResult(
                answer="Dịch vụ tạm thời không khả dụng. Vui lòng thử lại sau.",
                declined=True,
                citations=[],
                best_similarity=0.0,
                similarity_gap=0.0,
                rrf_scores=[],
                query_category=result.query_category,
                top_k_used=result.top_k_used,
                model_used=model,
                escalation_flag=False,
                chunks_before_compression=0,
                chunks_after_compression=0,
            )
        # Layer-1 guard (attempt 0 or budget-exhausted retry) — preserve the model_trace
        # write the original REJECTED branch performed (FR-010 observability).
        await _write_model_trace(
            db=db,
            model_name=model,
            guard_decision="REJECTED",
            best_similarity=result.best_similarity,
            similarity_gap=result.similarity_gap,
            top_k_used=result.top_k_used,
            query_category=result.query_category,
        )
        return RAGResult(
            answer=DECLINE_MESSAGE,
            declined=True,
            citations=[],
            best_similarity=result.best_similarity,
            similarity_gap=result.similarity_gap,
            rrf_scores=result.rrf_scores,
            query_category=result.query_category,
            top_k_used=result.top_k_used,
            model_used=model,
            escalation_flag=False,
            chunks_before_compression=result.chunks_before_compression,
            chunks_after_compression=result.chunks_after_compression,
        )

    # ── Accepted — generate the answer from the accepted retrieval ────────────────────────
    canonical_query = result.canonical_query
    query_vector = result.query_vector
    citations = result.citations
    # citations[i]["source_text"] is already "[sku] name\nprice_line\ndescription" (built by
    # search_and_retrieve) — identical to what this function used to rebuild from `compressed`.
    context = "\n\n".join(c["source_text"] for c in citations)
    messages = [
        {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"Product context:\n{context}\n\nCustomer question: {query}",
        },
    ]

    # LLM generation — timed for model_trace latency
    gen_start = time.perf_counter()
    llm_response = None
    try:
        llm_response = await AIGateway.complete(messages=messages, model=model)
        answer_text = llm_response.choices[0].message.content or DECLINE_MESSAGE
    except Exception as exc:
        logfire.error("LLM generation failed: {err}", err=str(exc))
        answer_text = DECLINE_MESSAGE
    gen_latency_ms = (time.perf_counter() - gen_start) * 1000

    # ── WP-V2-1 groundedness self-check (kill switch: GROUNDEDNESS_CHECK_ENABLED) ─────────
    grounded_declined = False
    grounded_meta: dict | None = None
    if llm_response is not None:
        from core.config import settings as _settings

        if _settings.GROUNDEDNESS_CHECK_ENABLED:
            (
                answer_text,
                llm_response,
                grounded_declined,
                grounded_meta,
            ) = await _apply_groundedness(
                query, context, messages, model, answer_text, llm_response
            )

    if grounded_declined:
        # Verdict says the answer would mislead (out-of-catalog subject, or
        # unsupported claims survived regeneration) → decline politely. The
        # unverified answer is NEVER cached.
        await _write_model_trace(
            db=db,
            model_name=model,
            guard_decision="GROUNDEDNESS_REJECTED",
            best_similarity=result.best_similarity,
            similarity_gap=result.similarity_gap,
            top_k_used=result.top_k_used,
            query_category=result.query_category,
            llm_response=llm_response,
            latency_ms=gen_latency_ms,
            extra_metadata=grounded_meta,
        )
        return RAGResult(
            answer=DECLINE_MESSAGE,
            declined=True,
            citations=[],
            best_similarity=result.best_similarity,
            similarity_gap=result.similarity_gap,
            rrf_scores=result.rrf_scores,
            query_category=result.query_category,
            top_k_used=result.top_k_used,
            model_used=model,
            escalation_flag=False,
            chunks_before_compression=result.chunks_before_compression,
            chunks_after_compression=result.chunks_after_compression,
        )

    # model_trace write — best-effort, captures gap + guard + cost (FR-010)
    await _write_model_trace(
        db=db,
        model_name=model,
        guard_decision="ACCEPTED",
        best_similarity=result.best_similarity,
        similarity_gap=result.similarity_gap,
        top_k_used=result.top_k_used,
        query_category=result.query_category,
        llm_response=llm_response,
        latency_ms=gen_latency_ms,
        extra_metadata=grounded_meta,
    )

    # Cache write — best-effort, never blocks the response (Article X.3). Only the FINAL
    # accepted query/answer is cached — intermediate rewrites are never cached (AC-2026-023)
    # since retrieve_with_retry only returns here on acceptance.
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
        best_similarity=result.best_similarity,
        similarity_gap=result.similarity_gap,
        rrf_scores=result.rrf_scores,
        query_category=result.query_category,
        top_k_used=result.top_k_used,
        model_used=model,
        escalation_flag=False,
        chunks_before_compression=result.chunks_before_compression,
        chunks_after_compression=result.chunks_after_compression,
    )


async def _apply_groundedness(
    query: str,
    context: str,
    messages: list[dict[str, str]],
    model: str,
    answer_text: str,
    llm_response: Any,
) -> tuple[str, Any, bool, dict]:
    """
    Why this exists: WP-V2-1 — verify → regenerate → decline loop around a generated
    answer so unsupported claims never reach the customer.
    What it does: Grades the answer with check_groundedness (1 economy call).
    - answerable=False → decline immediately (regeneration cannot conjure a product
      the catalog does not have).
    - supported=False → regenerate with STRICT_GROUNDING_SUFFIX up to
      settings.GROUNDEDNESS_MAX_REGEN times, re-grading each attempt; still
      unsupported → decline.
    Returns (answer_text, llm_response, declined, verdict_metadata). Never raises —
    check_groundedness is fail-open and regen failures abort to the current verdict.
    """
    from core.config import settings
    from services.rag.groundedness import STRICT_GROUNDING_SUFFIX, check_groundedness

    verdict = await check_groundedness(query, answer_text, context)
    regen_count = 0
    if verdict.answerable and not verdict.supported:
        strict_messages = [
            {"role": "system", "content": messages[0]["content"] + STRICT_GROUNDING_SUFFIX},
            *messages[1:],
        ]
        while regen_count < settings.GROUNDEDNESS_MAX_REGEN and not verdict.supported:
            regen_count += 1
            try:
                llm_response = await AIGateway.complete(messages=strict_messages, model=model)
                answer_text = llm_response.choices[0].message.content or DECLINE_MESSAGE
            except Exception as exc:
                logfire.error("groundedness regen failed: {err}", err=str(exc))
                break
            verdict = await check_groundedness(query, answer_text, context)

    declined = not (verdict.answerable and verdict.supported)
    meta = {
        "groundedness": {
            "answerable": verdict.answerable,
            "supported": verdict.supported,
            "unsupported_claims": verdict.unsupported_claims[:5],
            "regen_count": regen_count,
        }
    }
    return answer_text, llm_response, declined, meta


async def _write_retry_trace(
    db,
    attempt: int,
    rewritten_query: str,
    guard_decision: str,
    best_similarity: float,
    query_category: str,
) -> None:
    """
    Why this exists: Per-attempt observability for the RAG retry loop (agentic-rag-retry-loop,
    ticket 2026 — AC-2026-021).
    What it does: Persists the attempt number + rewritten query (search text, not a secret —
    no PII/tokens are ever logged here) + guard decision into `model_traces.metadata_`
    alongside the existing `_write_model_trace` fields. Best-effort — never raises, never
    blocks the retry loop.
    """
    try:
        from decimal import Decimal

        trace_row = ModelTrace(
            id=uuid7(),
            message_id=None,
            model_name="economy-chat",  # rewrite always runs on the light tier
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            latency_ms=0.0,
            cost=Decimal("0"),
            metadata_={
                "guard_decision": guard_decision,
                "attempt": attempt,
                "rewritten_query": rewritten_query,
                "best_similarity": round(best_similarity, 4),
                "query_category": query_category,
            },
        )
        db.add(trace_row)
        await db.flush()
        logfire.info(
            "retry model_trace written: attempt={a}, guard={g}",
            a=attempt,
            g=guard_decision,
        )
    except Exception as exc:
        logfire.warn("retry model_trace write failed (non-blocking): {err}", err=str(exc))


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
    extra_metadata: dict | None = None,
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
                **(extra_metadata or {}),
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
