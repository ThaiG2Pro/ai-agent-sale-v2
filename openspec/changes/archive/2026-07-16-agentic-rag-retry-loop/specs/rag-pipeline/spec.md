# rag-pipeline — Spec Delta (agentic-rag-retry-loop, ticket 2026)

> deltaMode requested: MODIFIED. `openspec/specs/rag-pipeline/` does not yet exist (no living RAG
> spec in this repo), so these requirements are authored under `## ADDED Requirements` to establish
> the capability spec. They describe the NEW agentic self-evaluate → rewrite → retry behavior that
> supersedes the current static single-pass pipeline (`services/rag/pipeline.py`). Flagged for SPEC LOCK.

## ADDED Requirements

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
