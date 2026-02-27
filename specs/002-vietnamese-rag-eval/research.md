# Research: Vietnamese RAG & Evaluation

**Feature**: `002-vietnamese-rag-eval`  
**Status**: Complete — all NEEDS CLARIFICATION resolved

---

## Decision 1: Hybrid Fusion Algorithm

**Decision**: Reciprocal Rank Fusion (RRF) with k=60  
**Formula**: `final_score = 1/(60 + rank_vector) + 1/(60 + rank_fts)`  
**Rationale**:
- RRF is rank-based (not score-based), so it naturally normalises the different
  score scales between cosine similarity (0–1) and ts_rank (arbitrary positive).
- k=60 is the well-established default from the original Cormack et al. (2009)
  paper and is effective across diverse IR tasks without dataset-specific tuning.
- Chunks absent from one source receive `max_rank + 1` as a penalty rank,
  preserving their contribution rather than dropping them entirely.
- Zero additional infrastructure required — all done in Python after two
  independent SQL queries.

**Alternatives considered**:
- *Linear combination*: Requires normalizing scores across two different scales;
  fragile when ts_rank distributions change with dataset size. Rejected.
- *Learned fusion (e.g., trained ranker)*: Requires labeled training data and
  a model serving layer. Over-engineered for Week 2. Rejected.
- *Borda Count*: Similar to RRF but no `k` smoothing, making it sensitive to
  single high-rank results. RRF is more robust. Rejected.

---

## Decision 2: Full-Text Search Configuration

**Decision**: PostgreSQL `simple` dictionary for both `to_tsvector` and
`plainto_tsquery`  
**Rationale**:
- The `simple` dictionary performs only lowercase conversion and stop-word
  removal (minimal stop-word list), making it language-agnostic.
- Vietnamese does not have an official PostgreSQL FTS dictionary. Using `simple`
  avoids `pg_ts_dict` errors while still enabling token matching.
- Mixed-language (VN/EN) queries work without branching logic.
- `plainto_tsquery` automatically handles multi-word phrases by inserting `&`
  operators, which is sufficient for product name/feature matching.

**Alternatives considered**:
- *`english` dictionary*: Applies English stemming, which breaks Vietnamese
  tokens. Rejected.
- *Custom Vietnamese dictionary (pg_trgm similarity)*: More accurate for VN but
  requires an additional PostgreSQL extension and maintenance. Deferred to
  post-Week 2.
- *Elasticsearch*: External service, violates Single-DB principle. Rejected.

---

## Decision 3: Query Classification Heuristic

**Decision**: Deterministic word-count + action-verb heuristic (no ML)  
**Rules**:
- `short`: word_count ≤ 5 → TopK 5
- `long`: 6 ≤ word_count ≤ 15 → TopK 15
- `ambiguous`: word_count > 15 AND (no action verb from `_ACTION_VERBS` frozenset
  AND no capitalised mid-sentence proper noun) → TopK 20
- `long` (safe fallback): > 15 words but action verb or proper noun present

**Action verbs** (bilingual, frozenset): `price`, `cost`, `compare`, `buy`,
`order`, `discount`, `ship`, `install`, `refund`, `warranty`, `available`,
`specs`, `feature`, `purchase`, `stock`, `quantity`, `giá`, `mua`, `đặt`,
`so sánh`, `giao`, `hoàn tiền`, `cài đặt`, `bảo hành`, `có sẵn`, `tính năng`,
`đặt hàng`, `kho`, `chiết khấu`

**Rationale**:
- Deterministic classification enables Article XII (Efficiency Metric) testing:
  tests can assert exactly which TopK was used without non-deterministic ML.
- The word-count boundaries (5/15) are practical for typical SME product queries
  (short = single-item lookup, long = comparison/feature query, ambiguous =
  open-ended exploration).

**Alternatives considered**:
- *ML-based intent classifier*: Non-deterministic, requires training data,
  violates Article III.1 for this deterministic function. Rejected.
- *Character count instead of word count*: Vietnamese words are shorter on
  average; word count is a more reliable proxy for query complexity. Rejected.

---

## Decision 4: Context Compression Strategy

**Decision**: Three-step pipeline — exact dedup → score filter → near-dup removal  
**Steps**:
1. **Exact dedup**: Remove chunks with identical `description` text (hash-based).
2. **Score filter**: Remove chunks where `vector_score < 0.5` (low confidence).
3. **Near-dup removal**: Sort remaining by `rrf_score` descending; remove any
   chunk with `SequenceMatcher.ratio() > 0.80` against an already-kept chunk.

**Rationale**:
- Step 1 is O(n) and zero-cost — catches copy-paste duplicate product entries.
- Step 2 (threshold 0.5) removes marginally relevant chunks that would dilute
  the context window, reducing hallucination risk and token cost.
- Step 3 (80% overlap) preserves the highest-ranked variant of near-duplicate
  product descriptions (e.g., a description reused across product variants).
- `SequenceMatcher` (difflib) is in the Python standard library — no new deps.

**Alternatives considered**:
- *TF-IDF cosine similarity for near-dup*: More accurate but requires building
  a local TF-IDF matrix. Overkill for chunk-level dedup. Rejected.
- *Sentence-transformer similarity for near-dup*: Requires embedding every chunk
  pair — O(n²) embedding calls. Too expensive. Rejected.
- *Single-step (score only)*: Misses exact duplicates that happen to have
  slightly different scores. Rejected.

---

## Decision 5: Confidence Guard Threshold

**Decision**: `best_similarity < 0.7` → decline  
**Rationale**:
- Constitution Article IX Section 9.3 explicitly mandates 0.7 as the threshold.
- `best_similarity` is the maximum cosine similarity among all retrieved chunks
  before compression — using the pre-compression value ensures the guard fires
  on the raw retrieval quality, not on post-filtered results.
- The guard also fires when `chunks_after_compression == 0` (all chunks
  eliminated), covering FR-016 edge cases #3 and #5.
- Boundary condition is **inclusive**: `>= 0.7` proceeds, `< 0.7` declines.

**Alternatives considered**:
- *Post-compression similarity*: Could return 0.0 if all chunks filtered out,
  giving a false "decline" even when some chunks were borderline. Rejected in
  favour of pre-compression best_similarity check.
- *Mean similarity across chunks*: A low-quality chunk pool could average below
  0.7 even with a strong best match. Best-of-k is the correct metric for RAG
  confidence. Rejected.

---

## Decision 6: Evaluation Scale

**Decision**: 5-point Likert scale for Tier 2 human evaluation  
**Scale**:
- 1 = Completely wrong / irrelevant
- 2 = Partially relevant, major errors
- 3 = Acceptable, minor gaps
- 4 = Good, nearly complete with citations
- 5 = Perfectly accurate, complete citations

**Rationale**:
- 5-point Likert is the industry standard for RAG human evaluation (as used
  in ARES, RAGAS human baselines, and LLM-as-judge research).
- Binary pass/fail loses nuance for borderline answers.
- `s` (skip) option allows human reviewers to flag cases they cannot judge
  (e.g., domain-specific products they don't know).
- Aggregate score = arithmetic mean of all non-skipped grades.

**Alternatives considered**:
- *Binary pass/fail*: Simpler but loses gradient information needed to track
  incremental improvements. Rejected.
- *LLM-as-judge (automated Tier 2)*: Costs API tokens and introduces circular
  evaluation if the same model grades itself. Deferred to Week 4+.
- *10-point scale*: Harder for reviewers to calibrate consistently. Rejected.

---

## Decision 7: Embedding Governance

**Decision**: Fixed dimension 1024 (bge-m3 via Ollama) per environment  
**Stored fields**: `model_name`, `model_version` (nullable), `created_at`  
**Rationale**:
- FR-014 and Constitution Article X.3 require embedding provenance.
- bge-m3 produces 1024-dimensional vectors and is the best-in-class multilingual
  model available for free via Ollama, supporting both Vietnamese and English.
- Dimension is fixed in the `Vector(1024)` column — any attempt to ingest
  embeddings of a different dimension will fail at the DB insert level.

**Alternatives considered**:
- *nomic-embed-text (768-dim)*: Also free via Ollama, but lower dimension and
  weaker multilingual support. Rejected for Vietnamese.
- *OpenAI text-embedding-3-small (1536-dim)*: Costs API tokens; violates
  Zero-Cost-First for dev. Deferred as a cloud fallback.

---

## Decision 8: FTS Over-fetch Strategy

**Decision**: `fetch_k = top_k * 2` for both vector and FTS before RRF merge  
**Rationale**:
- RRF requires ranked lists from both sources. If both are capped at `top_k`,
  chunks that appear only in one list may be excluded entirely.
- Fetching 2× provides merge headroom: the final merged result is then sliced
  to `top_k`.
- Doubling is a standard practice in RRF implementations (e.g., Elasticsearch
  hybrid search documentation).

---

## Resolved Edge Cases (FR-016)

| # | Edge Case | Handling |
|---|-----------|----------|
| 1 | Embedding model unavailable | Catch exception; return "Service unavailable" Vietnamese message; `declined=True` |
| 2 | Mixed VN/EN query | Process as-is; `simple` FTS and bge-m3 embedding handle both natively |
| 3 | Zero results from both vector and FTS | `best_similarity=0.0 < 0.7` → confidence guard fires; return DECLINE_MESSAGE |
| 4 | Query > 500 tokens | Truncate to first 500 words before embedding and FTS; log truncation |
| 5 | Compression reduces to empty | `chunks_after_compression == 0` → confidence guard fires; return DECLINE_MESSAGE |
