# rag-pipeline Specification

## Purpose
Vietnamese-first RAG pipeline for the SME sales agent: query classification/normalization,
two-tier semantic cache, hybrid retrieval (vector + Vietnamese FTS fused by RRF), context
compression, confidence gating, and a bounded agentic retry loop. This living spec is the
single source of truth for the tuned retrieval parameters; it supersedes the numeric values
in the original locked spec `specs/002-vietnamese-rag-eval/spec.md` (FR-009, FR-012, FR-013,
FR-015) via the spec-first repair recorded below (R-SDLC-001 remediation, 2026-07-16).

## Requirements

### Requirement: Layer-1 confidence gating at 0.45
The pipeline SHALL enforce a Layer-1 confidence guard: when the best vector similarity across
retrieved chunks is below `CONFIDENCE_THRESHOLD = 0.45`, the pipeline returns the standardized
decline message and MUST NOT generate an answer. The boundary is inclusive (best similarity
exactly 0.45 proceeds).

**Rationale (supersedes 0.7 in 002/FR-013).** The embedding model in production (`bge-m3`)
scores related cross-lingual content in the 0.35–0.70 cosine band, so a 0.7 gate declined most
legitimate Vietnamese product queries. 0.45 reliably rejects off-topic queries (~0.40 band)
while accepting on-topic ones. The hallucination risk in the 0.45–0.7 band that motivated the
original 0.7 gate is now mitigated by two newer mechanisms: (a) the bounded agentic retry loop
(this spec) rewrites and re-retrieves insufficient results instead of answering from weak
context, and (b) the Layer-2 fused guard (`AGENT_CONFIDENCE_THRESHOLD = 0.70` on
`(1-α)·similarity + α·rerank`) still blocks low-confidence answers post-generation. SME UX
feedback also preferred fewer false declines over marginally stricter gating.

#### Scenario: Off-topic query is declined
- **WHEN** all retrieved chunks have vector similarity < 0.45
- **THEN** the pipeline returns the predefined decline message without calling the answer LLM

#### Scenario: Boundary is inclusive
- **WHEN** the best chunk similarity is exactly 0.45
- **THEN** the pipeline proceeds toward answer generation (subject to the Layer-2 guard)

#### Scenario: Dual-layer ordering constraint holds
- **WHEN** `AGENT_CONFIDENCE_THRESHOLD` (Layer 2) is configured
- **THEN** it MUST be strictly greater than the Layer-1 threshold 0.45, otherwise Layer 2 is vacuous

### Requirement: Query classification bins and adaptive TopK
The pipeline SHALL classify every query into `short` / `long` / `ambiguous` using deterministic
word-count bins — `short: word_count ≤ 10`, `long: 11 ≤ word_count ≤ 20`, `ambiguous:
word_count > 20 AND no action verb AND no capitalized proper noun` (a >20-word query WITH an
action verb or proper noun falls back to `long`) — and compute adaptive TopK from the class:
short → 5, long → 15, ambiguous → 20, with intent overrides (e.g. COMPARISON → 10).

**Rationale (supersedes ≤5/6–15/>15 bins in 002/FR-009, FR-015).** Vietnamese product queries
are wordier than the English-oriented original bins assumed (analytic syllable-per-word
segmentation inflates `str.split()` counts); with ≤5-word "short" bins, routine questions like
"điện thoại này giá bao nhiêu tiền vậy shop" were misclassified as long/ambiguous and
over-fetched. The ≤10/11–20/>20 bins match observed query-length distribution on the gold set
while keeping the same three-class contract and TopK values.

#### Scenario: Short query gets TopK 5
- **WHEN** a query has ≤ 10 words
- **THEN** it is classified `short` and TopK = 5

#### Scenario: Long query gets TopK 15
- **WHEN** a query has 11–20 words
- **THEN** it is classified `long` and TopK = 15

#### Scenario: Ambiguous query gets TopK 20
- **WHEN** a query has > 20 words with no action verb and no capitalized proper noun
- **THEN** it is classified `ambiguous` and TopK = 20

#### Scenario: COMPARISON intent overrides TopK
- **WHEN** the normalized intent is COMPARISON
- **THEN** TopK = 10 regardless of word-count class

### Requirement: Context compression with relative threshold and 0.25 absolute floor
The pipeline SHALL compress retrieved context in three steps: (a) exact-text deduplication,
(b) low-signal filtering with a RELATIVE threshold `effective = max(0.25, best_similarity ×
0.65)` — keeping only chunks scoring ≥ 65% of the top retrieved hit, floored at the absolute
value `COMPRESSION_SCORE_THRESHOLD = 0.25`, and (c) near-duplicate removal for > 0.80 text
overlap, preserving the chunk with the highest RRF score.

**Rationale (supersedes the fixed `score < 0.5` cut in 002/FR-012).** A fixed 0.5 vector-score
cut silently discarded RRF-boosted FTS hits: chunks that rank highly via Vietnamese full-text
match legitimately carry low vector scores, and hybrid retrieval exists precisely to surface
them. The relative 65%-of-best rule keeps the original intent (drop low-signal tails — e.g.
best 0.79 still drops everything below 0.51) while the 0.25 floor prevents over-filtering when
the whole result set scores low.

#### Scenario: RRF-boosted FTS hit survives compression
- **WHEN** a chunk ranks high by FTS/RRF but has vector score 0.30 and `best_similarity` is 0.40
- **THEN** the chunk is retained (effective threshold = max(0.25, 0.26) = 0.26 ≤ 0.30)

#### Scenario: Low-signal tail is dropped relative to the best hit
- **WHEN** `best_similarity = 0.79` and a chunk scores 0.45
- **THEN** the chunk is removed (effective threshold = 0.79 × 0.65 ≈ 0.51)

#### Scenario: Near-duplicates keep the highest-RRF copy
- **WHEN** two chunks overlap by more than 80% of text
- **THEN** only the chunk with the higher RRF score is kept
### Requirement: Retrieval self-evaluation reusing existing confidence signals
The RAG pipeline SHALL evaluate whether a completed retrieval is sufficient to answer the query
**by reusing the existing confidence signals** (`best_similarity`, `chunks_after` compression count,
and the fused `confidence_score` computed by `confidence_node`). It MUST NOT introduce a second
numeric scoring model. When the result is sufficient, the pipeline SHALL accept it and MUST NOT enter
the retry loop (preserving current single-pass behavior). When the result is insufficient and retry
budget remains, the pipeline SHALL enter the retry loop instead of immediately declining.

#### Scenario: Sufficient first pass is accepted without a loop (AC-2026-001)
- **WHEN** the first retrieval returns `best_similarity ≥ LAYER1_CONFIDENCE_THRESHOLD` and `chunks_after > 0`
- **THEN** the pipeline accepts the result, does not call the grader/rewriter, and generates the answer as today

#### Scenario: Sufficiency decision reuses existing signals only (AC-2026-002)
- **WHEN** the pipeline decides whether a retrieval is sufficient
- **THEN** the decision is derived from `best_similarity`, `chunks_after`, and/or `confidence_score` — no new numeric scorer is added

#### Scenario: Insufficient first pass with budget enters the loop (AC-2026-003, AC-2026-005)
- **WHEN** the first retrieval is insufficient (`best_similarity < LAYER1_CONFIDENCE_THRESHOLD` OR `chunks_after == 0`) and `RAG_RETRY_MAX_ATTEMPTS > 0` with budget remaining
- **THEN** the pipeline attempts a query rewrite and re-retrieval before returning any decline

#### Scenario: Missing confidence signals are treated as insufficient, not a crash (AC-2026-006)
- **WHEN** retrieval yields no vector scores so `best_similarity` defaults to 0.0
- **THEN** the result is treated as insufficient (triggering retry if budget remains) and no exception propagates to the caller

#### Scenario: Grader malformed output is handled safely (AC-2026-004)
- **WHEN** the grader/rewriter returns malformed or unparseable output
- **THEN** the pipeline treats the attempt as insufficient/failed and falls back gracefully without raising

### Requirement: Intent-preserving query rewrite on the light model tier
When the loop is entered, the RAG pipeline SHALL rewrite the query using the **light model tier**
(`economy-chat` alias → `LIGHT_CHAT_MODEL`) and re-run retrieval with the rewritten query. The rewrite
MUST preserve the original question's intent and product entities; it MUST NOT switch to a premium/paid
tier by default, and MUST NOT change the subject of the question.

#### Scenario: Insufficient result triggers a light-tier rewrite and re-retrieval (AC-2026-007)
- **WHEN** a retrieval is judged insufficient and budget remains
- **THEN** the pipeline calls the light-tier model to rewrite the query and re-runs `search_and_retrieve` with the rewrite

#### Scenario: Rewrite preserves intent and entities across languages (AC-2026-008)
- **WHEN** a Vietnamese or English query is rewritten
- **THEN** the rewritten query keeps the same intent and the same product entity/subject as the original

#### Scenario: Successful rewrite yields an answer from improved retrieval (AC-2026-009)
- **WHEN** a rewritten retrieval becomes sufficient (`best_similarity ≥ LAYER1_CONFIDENCE_THRESHOLD`, `chunks_after > 0`)
- **THEN** the loop exits successfully and the answer is generated from the improved chunk set

#### Scenario: Grader/rewriter must run on the light tier (AC-2026-010, BR-2026-003)
- **WHEN** the grader/rewriter model is selected
- **THEN** it resolves to the light tier (`economy-chat` / `LIGHT_CHAT_MODEL`) and never a premium/paid tier by default

#### Scenario: Subject-drifting rewrite is discarded (BR-2026-004)
- **WHEN** a rewrite changes the question's subject to a different product
- **THEN** the rewrite is rejected and not used for re-retrieval

### Requirement: Bounded, terminating retry budget
The retry loop SHALL be hard-capped by the configurable setting `RAG_RETRY_MAX_ATTEMPTS`
(integer, range 0..2, default 1) and MUST terminate on cap, no-progress, or failure. It MUST NOT
run additional retrieval after the cap, and the total added LLM/embedding work MUST be provably
bounded by the cap.

#### Scenario: Loop never exceeds the configured cap (AC-2026-013, AC-2026-017, BR-2026-001)
- **WHEN** `RAG_RETRY_MAX_ATTEMPTS = N` (N in 0..2) and retrievals stay insufficient
- **THEN** the pipeline performs at most N rewrite+retrieval attempts and the added LLM/embed calls are ≤ N × (1 rewrite + 1 embed/search)

#### Scenario: Zero cap disables the loop (kill switch) (AC-2026-014, BR-2026-007)
- **WHEN** `RAG_RETRY_MAX_ATTEMPTS = 0`
- **THEN** the pipeline behaves exactly like the current static single-pass flow — no grader, no rewrite, no retry

#### Scenario: No-progress stops the loop early (AC-2026-012, AC-2026-015, BR-2026-005)
- **WHEN** a rewrite produces an empty/identical query, or the re-retrieval returns the same top `chunk_id`s, or `best_similarity` does not improve
- **THEN** the loop stops immediately even if budget remains, avoiding wasted work

#### Scenario: Budget exhausted while still insufficient (AC-2026-016, BR-2026-009)
- **WHEN** all attempts are used and the result is still below threshold
- **THEN** the pipeline returns the current decline behavior (does not lower the confidence bar to force a weak answer) [ASSUMED default — confirm at SPEC LOCK]

#### Scenario: Mid-loop model/embedding failure aborts cleanly (AC-2026-011, AC-2026-018)
- **WHEN** the grader/rewriter or embedding service fails or times out mid-loop
- **THEN** the loop aborts, returns the best result seen so far (or declines as today), and leaves no partial/corrupt state

### Requirement: Preservation of fast-paths and observability
The retry loop SHALL NOT alter existing pipeline fast-paths and SHALL record per-attempt
observability. Cache hits, the `is_valid` spam guard, and the embedding-unavailable path MUST bypass
the loop. The COMPARISON split fallback MUST continue to work without redundant double retrieval, and
each retry attempt MUST be traced.

#### Scenario: Cache hit skips the loop (AC-2026-019, BR-2026-006)
- **WHEN** an L1 or L2 semantic cache hit occurs
- **THEN** the cached answer is returned and the loop (grader + rewrite) is never invoked

#### Scenario: Spam/gibberish is declined before any rewrite (AC-2026-022, BR-2026-006)
- **WHEN** `normalize_query` marks the query `is_valid = false`
- **THEN** the pipeline declines before entering the loop and never rewrites spam

#### Scenario: COMPARISON split and retry loop are mutually exclusive (AC-2026-020, INT-2026-006)
- **WHEN** a COMPARISON-intent query would trigger both the existing split fallback and the retry loop
- **THEN** only one retrieval-recovery mechanism runs for that turn — no duplicated retrieval storm

#### Scenario: Each retry attempt is traced (AC-2026-021, BR-2026-008)
- **WHEN** a retry attempt runs
- **THEN** a `model_trace` row records the attempt number, the rewrite, and the guard decision, with no PII/tokens in logs

#### Scenario: Only the final accepted result is cached (AC-2026-023)
- **WHEN** the loop finishes and produces an accepted answer
- **THEN** only the final accepted query/answer is written to the semantic cache — intermediate rewrites are not cached

#### Scenario: Rewritten queries stay token-bounded (AC-2026-024, BR-2026-010)
- **WHEN** a rewritten query exceeds 500 words or has high Vietnamese token density
- **THEN** the FTS 500-word truncation applies to each rewritten query so token growth stays bounded

