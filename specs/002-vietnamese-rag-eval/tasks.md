# Tasks: Vietnamese RAG & Evaluation

**Input**: Design documents from `/specs/002-vietnamese-rag-eval/`  
**Branch**: `002-vietnamese-rag-eval`  
**Spec**: [spec.md](./spec.md) | **Plan**: [plan.md](./plan.md)

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no shared dependencies)
- **[Story]**: User story this task belongs to (US1–US5)
- **[x]**: Already implemented and verified
- File paths are relative to repo root

---

## Phase 1: Setup (Environment Verification)

**Purpose**: Confirm the Week 1 foundation is live before starting Week 2 work.

- [x] T001 Verify Docker Compose stack starts cleanly: `docker compose up -d` — postgres must reach "healthy"
- [x] T002 Verify schema migration is current: `uv run alembic upgrade head` — no pending migrations
- [x] T003 Verify pgvector extension active: `SELECT extversion FROM pg_extension WHERE extname='vector'` returns 0.8+
- [x] T004 Verify Ollama serving bge-m3 + qwen model: `curl http://localhost:11434/api/tags` shows both models

**Checkpoint**: Infrastructure ready — all four checks green.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core service primitives required by all user stories.

**⚠️ CRITICAL**: All user story phases depend on these being complete.

- [x] T005 `models/schema.py` — `Product`, `TextEmbedding`, `SemanticCache`, `ConversationMessage`, `ModelTrace` SQLAlchemy models in `agent_v1` schema
- [x] T006 `services/database.py` — `AsyncSessionLocal`, `engine`, `get_db` dependency — async SQLAlchemy engine with asyncpg driver
- [x] T007 `services/semantic_cache.py` — `canonicalize_query`, `generate_query_hash`, `get_l1_cache`, `get_l2_cache`, `set_cache` — SHA256 L1 + pgvector L2 cache
- [x] T008 `services/ai.py` — `NormalizedQuery` Pydantic model (FR-004) + `AIGateway.complete`, `AIGateway.embed`, `AIGateway.normalize_query` via LiteLLM Router
- [x] T009 `core/ai_config.py` — LiteLLM config with `economy-chat`, `economy-embedding`, `premium-chat` model aliases — no hard-coded model names in service code
- [x] T010 `services/rag.py` — `_ACTION_VERBS` frozenset, `_RRF_K=60`, `_CONFIDENCE_THRESHOLD=0.7`, `_COMPRESSION_SCORE_THRESHOLD=0.5`, `_NEAR_DUP_THRESHOLD=0.80` constants and `DECLINE_MESSAGE` Vietnamese string
- [x] T011 `services/rag.py` — `classify_query(query)` deterministic classifier: ≤5 words→`short`, 6–15→`long`, >15 words AND no action verb AND no proper noun→`ambiguous` (FR-015, spec.md Section 3.2). **Hybrid Heuristic Alignment (I1 FIXED)**: Exact match to spec.md definition ensures SC-004 testability.
- [x] T012 `services/rag.py` — `compute_adaptive_topk(query)` maps `short`→5, `long`→15, `ambiguous`→20 (FR-009)
- [x] T013 `services/rag.py` — `_overlap_ratio(a, b)` using `SequenceMatcher.ratio()` — private helper for near-dup detection
- [x] T014 `services/rag.py` — `compress_context(chunks)` three-step pipeline: (1) exact-text dedup by `description`, (2) remove `vector_score < 0.5`, (3) remove near-dups with `ratio > 0.80` keeping highest `rrf_score` (FR-012)
- [x] T015 `services/rag.py` — `hybrid_search_rrf(db, query_vector, query_text, top_k)` — over-fetches 2× from vector search (SQLAlchemy ORM) + FTS (`plainto_tsquery('simple', ...)`) then merges via RRF formula (FR-005)
- [x] T016 `services/rag.py` — `RAGResult` Pydantic model with all 11 fields: `answer`, `declined`, `citations`, `best_similarity`, `rrf_scores`, `query_category`, `top_k_used`, `model_used`, `escalation_flag`, `chunks_before_compression`, `chunks_after_compression` (FR-011)
- [x] T017 `tests/unit/test_rag_helpers.py` — 40 TDD unit tests for `classify_query`, `compute_adaptive_topk`, `_overlap_ratio`, `compress_context`, `RAGResult` — all passing with `uv run pytest tests/unit/test_rag_helpers.py`

- [x] **T017b [G1 COVERAGE GAP]** `services/rag.py` — Add keyword extraction during ingestion (FR-003): In `ingest_product_text()`, after embedding the full product description, call `AIGateway.extract_keywords(description, count=5)` to extract up to 5 key terms and store them in the `TextEmbedding` model under a new `keywords` column (JSON array). This enables FTS enrichment during inference and supports product discovery. Wrap in `try/except` — if extraction fails, proceed with empty keywords list. **(NEW TASK: Ingestion Keyword Extraction Coverage G1)**

**Checkpoint**: Foundation complete — `uv run pytest tests/unit/test_rag_helpers.py` → 40/40 PASS.

---

## Phase 3: User Story 1 — Vietnamese Product Query Answering (Priority: P1) 🎯 MVP

**Goal**: A customer sends a Vietnamese product query and receives an accurate, cited answer generated from the product database. The answer must include ProductID + ChunkID citations and must not hallucinate.

**Independent Test**: `pytest tests/integration/test_rag.py::test_answer_with_rag_returns_citations` — ingests a Vietnamese product, sends a Vietnamese query, verifies `RAGResult.declined=False`, `RAGResult.citations` is non-empty, and the answer is not the DECLINE_MESSAGE.

### Step 1: Wire query normalization into the RAG pipeline

- [x] T018 [US1] `services/rag.py` — In `answer_with_rag()`, add Step 0 before the embedding call: call `AIGateway.normalize_query(query)` to get a `NormalizedQuery`, log the detected language and intent, and use `normalized.canonical` as the embedding input and `" ".join(normalized.extracted_keywords)` to enrich the FTS `query_text`. Wrap in `try/except` — on failure, fall back to raw `query` with no interruption to the pipeline. (FR-004, FR-007)

### Step 2: Wire semantic cache (L1 → L2) before retrieval

- [x] T019 [US1] `services/rag.py` — In `answer_with_rag()`, add L1 cache check immediately after normalization and before the embedding call: call `get_l1_cache(db, normalized.canonical)`. If a hit is returned, construct and return a `RAGResult` with `answer=cached_response`, `declined=False`, `citations=stored_citations_from_cache` (retrieve from the cache entry's `citations` column), `best_similarity=1.0`, `query_category` and `top_k_used` from the already-computed `classify_query` result. Import `get_l1_cache` from `services.semantic_cache`. (FR-007, Article IX, Article X.3) **Citation Provenance (C1 FIXED)**: Cache hits must return persisted citations to satisfy Article IX.

- [x] T020 [US1] `services/rag.py` — In `answer_with_rag()`, add L2 cache check after the embedding succeeds but before `hybrid_search_rrf`: call `get_l2_cache(db, query_vector, threshold=0.95)`. If a hit is returned, return early with same `RAGResult` structure as L1 hit, including `citations=stored_citations_from_cache` retrieved from the cache entry. This avoids a full hybrid retrieval + LLM call on semantically identical queries. (FR-007, Article IX, Article X.3) **Citation Provenance (C1 FIXED)**: L2 cache hits must also return persisted citations.

### Step 3: Wire cache write after successful answer

- [x] T021 [US1] `services/rag.py` — In `answer_with_rag()`, after a successful (non-declined) LLM answer is generated, call `set_cache(db, query=normalized.canonical, response=answer_text, embedding=query_vector, model_name=model)` as a best-effort fire-and-forget (wrap in `try/except`, log warning on failure but do NOT raise). This ensures future identical queries hit L1/L2 cache. Import `set_cache` from `services.semantic_cache`. (Article X.3)

### Step 4: Expose RAG query via FastAPI

- [x] T022 [P] [US1] `api/routes/query.py` — Create new file. Define `QueryRequest(BaseModel)` with field `query: str = Field(..., min_length=1, max_length=2000)` and `QueryResponse(BaseModel)` mirroring `RAGResult` fields (answer, declined, citations, best_similarity, query_category, top_k_used, model_used). Add docstrings explaining business purpose (Article XI).
  Note: The max_length=2000 is a character-based heuristic for the >500 tokens truncation edge case in the spec. Approximate mapping: 4 characters ≈ 1 token for Vietnamese/English. Recommend documenting this assumption or moving truncation logic into the Pydantic validator for explicit token control.

- [x] T023 [US1] `api/routes/query.py` — Implement `POST /query` FastAPI endpoint: takes `QueryRequest`, calls `await answer_with_rag(db, request.query)`, returns `QueryResponse`. No authentication required (public customer endpoint). Use `Depends(get_db)` for the DB session. Add `tags=["query"]`.

- [x] T024 [US1] `api/main.py` — Import `query` router from `api.routes` and add `app.include_router(query.router)` after the existing `admin.router` registration. One-line addition, no other changes. **(I2 FIXED)**: Reference is now positional (after admin router) rather than line number.

### Checkpoint test (US1)

- [x] T025 [US1] `tests/integration/test_rag.py` — Add async test function `test_answer_with_rag_returns_citations()`: ingest a Vietnamese product (`name="Widget Pro Vietnam"`, `sku=f"vn-wp-{uuid4()}"`, `description="Widget Pro có tính năng bảo hành 2 năm và hỗ trợ Bluetooth"`), call `answer_with_rag(db, "Widget Pro có bảo hành không?")`, assert `result.declined == False`, `len(result.citations) > 0`, `result.best_similarity >= 0.5`, `result.chunks_before_compression >= 1`. Skip on Ollama offline.

---

## Phase 4: User Story 2 — Hybrid Search Outperforms Vector-Only (Priority: P2)

**Goal**: Prove that hybrid RRF retrieval surfaces more relevant product chunks than vector-only, especially for keyword-heavy Vietnamese product queries.

**Independent Test**: `pytest tests/integration/test_rag.py::test_hybrid_surfaces_keyword_match` — ingests a product with a specific Vietnamese keyword in its name, runs a query containing that keyword, and verifies the product appears in the hybrid results.

### Step 1: Add FTS index for production performance

- [ ] T026 [US2] `migrations/versions/` — Generate a new Alembic migration: `uv run alembic revision --autogenerate -m "add_gin_index_products_fts"`. Then manually edit the generated file to add a GIN index for full-text search on products. The index expression is: `to_tsvector('simple', COALESCE(name,'') || ' ' || COALESCE(description,''))`. Use `CREATE INDEX IF NOT EXISTS idx_products_fts ON agent_v1.products USING gin(...)` (no CONCURRENTLY in migrations). Columns indexed: `name` + `description` with the `simple` dictionary (language-agnostic, works for Vietnamese and English). The corresponding `downgrade()` should `DROP INDEX IF EXISTS agent_v1.idx_products_fts`. **(A1 FIXED)**: Explicit column names and dictionary specified.

### Checkpoint tests (US2)

- [x] T027 [US2] `tests/integration/test_rag.py` — Add async test `test_hybrid_search_rrf_returns_ranked_results()`: ingest 3 products with distinct descriptions, call `hybrid_search_rrf(db, query_vector, query_text="widget bluetooth", top_k=3)`, assert: (1) returns a list of dicts, (2) each dict has keys `chunk_id`, `product_id`, `rrf_score`, `vector_score`, (3) results are sorted by `rrf_score` descending, (4) all `rrf_score` values are > 0. Skip on Ollama offline.

- [x] T028 [US2] `tests/integration/test_rag.py` — Add async test `test_hybrid_surfaces_fts_keyword_match()`: ingest one product with description containing exact Vietnamese term `"kết nối Bluetooth"`, call `hybrid_search_rrf()` with a query text `"Bluetooth"` and a random (unlikely) query vector so vector search ranks it low. Assert the product appears in the top-3 results thanks to FTS. This validates that RRF rescues FTS-only hits. Skip on Ollama offline.

---

## Phase 5: User Story 3 — Confidence-Gated Responses (Priority: P3)

**Goal**: Out-of-scope queries (similarity < 0.7) must receive a polite Vietnamese decline message instead of a hallucinated product answer. Zero hallucinations on zero-result queries.

**Independent Test**: `pytest tests/integration/test_rag.py::test_confidence_guard_declines_unknown_query` — sends a query about a non-existent product to an empty or unrelated database partition and asserts `declined=True`.

### Checkpoint tests (US3)

- [x] T029 [US3] `tests/integration/test_rag.py` — Add async test `test_confidence_guard_declines_low_similarity()`: call `answer_with_rag(db, "xyzzy thần kỳ không tồn tại")` against an empty database (use the existing test cleanup fixture). Assert: `result.declined == True`, `result.answer == DECLINE_MESSAGE` (import from `services.rag`), `result.citations == []`, `result.best_similarity < 0.7`. Skip on Ollama offline.

- [x] T030 [US3] `tests/integration/test_rag.py` — Add async test `test_compression_to_empty_triggers_decline()`: create a `RAGResult` scenario by calling `compress_context([])` directly (unit-level), assert it returns `[]`, then verify in `answer_with_rag()` that passing empty chunks triggers `declined=True`. This can be a pure unit test: mock `hybrid_search_rrf` to return `[]`, verify `answer_with_rag` returns `RAGResult(declined=True)`. Use `unittest.mock.AsyncMock` to patch.

---

## Phase 6: User Story 4 — Adaptive Context & TopK (Priority: P4)

**Goal**: Short/long/ambiguous queries automatically get TopK 5/15/20 respectively. Context compression reduces token count by ≥20% vs passing all raw chunks.

**Independent Test**: `pytest tests/unit/test_rag_helpers.py` → 40/40 PASS (already implemented). New: Article XII efficiency test.

### Step 1: Enrich gold dataset for Article XII compliance

- [x] T031 [P] [US4] `tests/eval/gold_dataset.json` — Add `"difficulty": "easy"` to items `sme_001`, `sme_002`, `sme_006`, `sme_007`, `sme_010`, `vn_001`, `vn_002`, `vn_004`, `vn_005`, `vn_008`. Add `"difficulty": "hard"` to items `sme_003`, `sme_004`, `sme_009`, `vn_003`, `vn_006`, `vn_007`. Add `"difficulty": "medium"` to remaining items. Each easy item should also assert `complexity == "easy"` (already present) — just add the `difficulty` key consistently across all 20 items.

- [x] T032 [P] [US4] `tests/eval/gold_dataset.json` — Add `"language": "en"` field to the 10 English items (`sme_001` through `sme_010`) so all items have an explicit `language` field. Vietnamese items (`vn_001`–`vn_010`) already have `"language": "vi"`. This aligns with the `EvaluationRecord` entity in `data-model.md`.

### Step 2: Article XII efficiency assertion

- [x] **T033+T034 (Consolidated) [US4]** `tests/unit/test_rag_helpers.py` — Add new test class `TestArticleXIIEfficiency` with the following tests (moved from redundant T033/T034):
  1. `test_easy_query_topk_is_minimal()`: given a short easy query (`"giá"`, 1 word), assert `compute_adaptive_topk("giá") == 5` and `classify_query("giá") == "short"`. Verify TopK 5 is the minimum.
  2. `test_ambiguous_query_topk_is_maximal()`: given a long ambiguous query (>15 words, no action verb), assert `compute_adaptive_topk(" ".join(["word"]*16)) == 20`. Verify TopK 20 is the maximum.
  3. `test_compression_reduces_token_count()`: create 10 chunks (5 with `vector_score=0.9`, 3 with `score<0.5`, 2 exact duplicates), call `compress_context(chunks)`, assert `len(result) <= 5` (≥50% reduction, exceeds SC-005 20% target), assert no duplicate `description` strings.

  **(D1 FIXED)**: Consolidated to avoid redundancy with T017 while documenting Article XII compliance.

---

## Phase 7: User Story 5 — Evaluation CLI for Human Grading (Priority: P5)

**Goal**: A developer runs `uv run python scripts/tier1_eval.py --skip-tier2` against the 20-item gold dataset and gets a structured JSON report with Tier 1 pass rates.

**Independent Test**: `pytest tests/unit/test_eval_cli.py::test_tier1_eval_imports_cleanly` — imports the eval module without error, verifies gold dataset exists and has ≥20 items, verifies `reports/` directory will be created.

### Step 1: Add --verbose flag to eval CLI

- [x] T035 [US5] `scripts/tier1_eval.py` — Add `--verbose` CLI flag via `argparse`. When `--verbose` is set, after printing the answer snippet for each item, also print: `f"  Model     : {rag_result.model_used}"` and `f"  Escalated : {rag_result.escalation_flag}"`. This satisfies Article XII's requirement to surface model routing decisions during evaluation.

### Checkpoint test (US5)

- [x] T036 [US5] `tests/unit/test_eval_cli.py` — Create new file. Add `test_gold_dataset_structure()`: load `tests/eval/gold_dataset.json`, assert `len(items) >= 20`, assert every item has keys `id`, `query`, `expected_keywords`, `complexity`. Assert at least 5 items have `language == "vi"`. Add `test_gold_dataset_has_difficulty_field()`: after T031 completes, assert every item has `difficulty` in `{"easy", "medium", "hard"}`. Add `test_tier1_eval_module_imports()`: do `import scripts.tier1_eval` without error (or `importlib.util.spec_from_file_location`). All three tests are pure file/import checks — no DB, no Ollama.

---

## Final Phase: Polish & Cross-Cutting Concerns

**Purpose**: Ensure lint, docs, and end-to-end consistency after all stories are complete.

- [x] T037 [P] `specs/002-vietnamese-rag-eval/quickstart.md` — Update Step 3 "Ingest Sample Products" to reference `uv run python scripts/seed_bulk.py --total 50` (the actual script) instead of the nonexistent `scripts/seed_products.py`. No other content changes.

- [x] T038 [P] `services/rag.py` — Add inline comment above the `answer_with_rag()` signature documenting the updated 9-step flow: `# Flow: normalize → L1 cache → embed → L2 cache → truncate → hybrid_search_rrf → compress → confidence_guard → answer → cache_write`. This helps future maintainers understand the full pipeline at a glance (Article XI).

- [x] T039 `services/rag.py` — After completing T018–T021 (normalize + cache wiring), run `uv run ruff check services/rag.py` and fix any new E501 or import-order issues introduced. Run `uv run ruff format services/rag.py` to normalize formatting.

- [x] T040 `tests/` — Run full deterministic test suite: `uv run pytest tests/unit/ -v`. Confirm all tests pass (expected: ≥43 tests). Fix any import errors caused by new imports in T018–T024 modifications.

---

## Dependencies (Story Completion Order)

```
Phase 2 (Foundation)
    │
    ├── Phase 3 (US1: Query Answering) ← MUST come first: other stories need answer_with_rag
    │       │
    │       ├── Phase 4 (US2: Hybrid Search) ─────────────────────────────── parallel after US1 foundation
    │       ├── Phase 5 (US3: Confidence Guard) ──────────────────────────── parallel after US1 foundation
    │       ├── Phase 6 (US4: Adaptive TopK) ─────────────────────────────── parallel after US1 foundation
    │       └── Phase 7 (US5: Evaluation CLI) ────────────────────────────── parallel after US1 foundation
    │
    └── Final Phase (Polish) ← requires all stories complete
```

**Critical path**: T018 → T019 → T020 → T021 (must be sequential — all modify `answer_with_rag()`)  
**Then parallel**: T022/T026/T031/T032 can run simultaneously (different files)

---

## Parallel Execution Examples

### Sprint 1 (complete US1 wiring first — sequential):
```
T018 → T019 → T020 → T021 (answer_with_rag modifications — sequential)
T022 (api/routes/query.py — can start after T016 is done, parallel to T018-T021)
```

### Sprint 2 (after T021 merged — all parallel):
```
T025 (integration test US1)     ← developer A
T026 + T027 + T028 (US2 tests)  ← developer B
T029 + T030 (US3 tests)         ← developer C
T031 + T032 + T033 (US4 enrich) ← developer D
T035 + T036 (US5 polish)        ← developer E
```

---

## Implementation Strategy

**MVP scope**: US1 tasks T018–T025 deliver a fully wired, cache-integrated, citation-producing RAG endpoint. This is the minimum needed to demonstrate the full Week 2 pipeline.

**Incremental delivery**:
1. T018–T021: Wire normalize + cache → makes `answer_with_rag()` production-ready
2. T022–T024: Expose via FastAPI → makes the feature externally accessible
3. T025: Integration test → validates the MVP end-to-end
4. T026–T028: US2 hybrid search tests → validates retrieval quality
5. T029–T030: US3 confidence guard tests → validates safety behavior
6. T031–T034: US4 gold dataset + efficiency → validates Article XII compliance
7. T035–T036: US5 eval CLI polish → validates evaluation tooling
8. T037–T040: Polish → clean state for Week 3 handoff

---

## Task Summary

| Phase | User Story | New Tasks | Completed | Total |
|-------|-----------|-----------|-----------|-------|
| Setup | — | 0 | 4 | 4 |
| Foundation | — | 0 | 13 | 13 |
| Phase 3 | US1 (P1) | 8 | 0 | 8 |
| Phase 4 | US2 (P2) | 3 | 0 | 3 |
| Phase 5 | US3 (P3) | 2 | 0 | 2 |
| Phase 6 | US4 (P4) | 4 | 0 | 4 |
| Phase 7 | US5 (P5) | 2 | 0 | 2 |
| Polish | — | 4 | 0 | 4 |
| **Total** | | **23** | **17** | **40** |

**Parallel opportunities**: 8 tasks marked `[P]`  
**Estimated total for remaining 23 tasks**: ~5–6 hours at ≤15 min/task  
**Suggested MVP**: Complete US1 (T018–T025) first — delivers a working, cache-integrated, cited RAG endpoint
