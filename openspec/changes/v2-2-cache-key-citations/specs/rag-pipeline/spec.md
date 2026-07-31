# rag-pipeline — Spec Delta (v2-2-cache-key-citations, WP-V2-2)

> Fast-track docs sync (đã implement + đo — unit 407 pass, Tier-R 34/34, Tier-F 12/12 giữ nguyên).
> Một requirement mới + một requirement sửa đổi, merge vào `openspec/specs/rag-pipeline/spec.md`
> khi archive.

## MODIFIED Requirements

### Requirement: Semantic cache L1 keys on the deterministic raw query
The L1 exact-match cache lookup in `search_and_retrieve` SHALL hash the RAW user query
(strip + lowercase via `canonicalize_query`) and SHALL run BEFORE the LLM normalize step, so an
L1 hit costs zero chat calls and the key never depends on non-deterministic LLM output. Cache
writes in `answer_with_rag` SHALL key on the same raw query. The graph-path cache write
(`answer_node._write_cache`) SHALL key on `canonical_query`, which in that path equals the
pronoun-expanded query the lookup hashed (normalize is skipped when intent is pre-classified) —
keying on raw `user_message` is forbidden there because pronoun queries ("nó giá bao nhiêu")
are context-dependent and would replay across products. L2 vector lookup remains on the raw
query embedding (unchanged).

#### Scenario: Same raw query hits L1 despite normalize variance
- **GIVEN** a query was answered and cached, and the LLM normalizer returns a DIFFERENT
  canonical form for the same text on the next call
- **WHEN** the identical raw query arrives again
- **THEN** the L1 lookup hits (raw-keyed hash), the cached answer + citations are returned,
  and the normalize LLM call is skipped entirely

#### Scenario: Pronoun query is not poisoned across products
- **GIVEN** customer A asked "nó giá bao nhiêu" in a Samsung context (expanded + answered)
- **WHEN** the answer is cached from the graph path
- **THEN** the cache key is the EXPANDED query ("Samsung S24 Ultra giá bao nhiêu"), so
  customer B's "nó giá bao nhiêu" in an iPhone context cannot hit the Samsung answer

## ADDED Requirements

### Requirement: Fragment-level citations (FR-011)
After an answer is accepted (post-groundedness), each citation SHALL carry an optional
`fragment_text` field: the single sentence of its `source_text` most similar to the answer
(deterministic `SequenceMatcher`, no LLM call; `None` when the best ratio is below
`MIN_FRAGMENT_RATIO = 0.35`). The `Citation` boundary model SHALL declare
`fragment_text: str | None = None` so cached citation dicts containing the field survive
`Citation(**dict)` re-validation in `retrieval_node`. Citations persisted to the semantic
cache SHALL be annotated before the write so cache hits replay fragment-level grounding.

#### Scenario: Live answer citations carry the grounding sentence
- **WHEN** `answer_with_rag` accepts a generated answer
- **THEN** every citation in `RAGResult.citations` has `fragment_text` set to the source
  sentence best matching the answer (or `None` when nothing clears the ratio floor)

#### Scenario: Cache replay preserves fragments
- **GIVEN** an accepted answer whose citations were fragment-annotated and cached
- **WHEN** the same raw query hits L1 later
- **THEN** the replayed citations contain the same `fragment_text` values

### Requirement: Failed generations are never cached
When answer generation fails and the pipeline falls back to an error/decline message
(`llm_response is None` in `answer_with_rag`; `metrics is None` in `answer_node`), the semantic
cache write SHALL be skipped — caching the fallback would replay a fake decline for every future
hit of that query until TTL expiry (observed live under provider 429 throttling).

#### Scenario: Rate-limited generation does not poison the cache
- **GIVEN** the chat LLM raises (e.g. 429) during answer generation for a query
- **WHEN** the pipeline returns the fallback DECLINE_MESSAGE
- **THEN** no semantic-cache entry is written, and the next identical query retries generation

#### Scenario: Pre-V2-2 cache entries stay compatible
- **GIVEN** a cache entry written before this change (citations without `fragment_text`)
- **WHEN** it is replayed and re-validated as `Citation` models
- **THEN** validation succeeds with `fragment_text = None` (optional field, no migration)
