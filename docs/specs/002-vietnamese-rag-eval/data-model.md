# Data Model: Vietnamese RAG & Evaluation

**Feature**: `002-vietnamese-rag-eval`  
**Schema**: `agent_v1` (PostgreSQL 17)  
**Principle**: Single-DB — all state lives in PostgreSQL, no auxiliary stores

---

## Entity Diagram

```
┌─────────────────────┐        ┌──────────────────────────┐
│      products       │1      *│     text_embeddings       │
│─────────────────────│────────│──────────────────────────│
│ id          UUID PK │        │ id           UUID PK      │
│ sku         VARCHAR │        │ source_id    UUID FK      │
│ name        VARCHAR │        │ source_type  VARCHAR      │
│ description TEXT    │        │ embedding    vector(1024) │
│ price       NUMERIC │        │ model_name   VARCHAR      │
│ metadata    JSONB   │        │ model_version VARCHAR     │
│ created_at  TIMESTAMPTZ│     │ created_at   TIMESTAMPTZ │
│ updated_at  TIMESTAMPTZ│     └──────────────────────────┘
└─────────────────────┘
         │
         │ (implicit — citation metadata stored in ConversationMessage)
         ▼
┌─────────────────────────────┐       ┌────────────────────────┐
│   conversation_sessions     │1     *│  conversation_messages  │
│─────────────────────────────│───────│────────────────────────│
│ id           UUID PK        │       │ id              UUID PK │
│ external_id  VARCHAR unique │       │ session_id      UUID FK │
│ metadata     JSONB          │       │ role            VARCHAR │
│ created_at   TIMESTAMPTZ    │       │ content         TEXT    │
└─────────────────────────────┘       │ token_count     INT     │
                                      │ model_name      VARCHAR │
                                      │ source_chunk_ids JSONB  │ ← Article IX
                                      │ metadata        JSONB   │
                                      │ created_at  TIMESTAMPTZ │
                                      └────────────────────────┘
                                               │1
                                               │*
                                      ┌────────────────────┐
                                      │    model_traces     │
                                      │────────────────────│
                                      │ id             UUID │
                                      │ message_id     UUID │
                                      │ model_name  VARCHAR │
                                      │ prompt_tokens  INT  │
                                      │ completion_tokens INT│
                                      │ total_tokens   INT  │
                                      │ latency_ms   FLOAT  │
                                      │ cost        NUMERIC │
                                      │ metadata      JSONB │
                                      └────────────────────┘

┌─────────────────────────────────────────────────────────┐
│                    semantic_cache                        │
│─────────────────────────────────────────────────────────│
│ query_hash      VARCHAR(64) PK   (SHA-256 of canonical) │
│ query_text      TEXT                                    │
│ response        TEXT                                    │
│ embedding       vector(1024)                            │
│ model_name      VARCHAR                                 │
│ citations       JSONB                                   │
│ similarity_score FLOAT                                  │
│ created_at      TIMESTAMPTZ                             │
└─────────────────────────────────────────────────────────┘
```

---

## Entity Definitions

### `products` — Source knowledge base

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | UUID | PK, default uuid4 | Internal product identifier |
| `sku` | VARCHAR(50) | UNIQUE, NOT NULL, indexed | Business SKU for citation metadata |
| `name` | VARCHAR(255) | NOT NULL | Product display name |
| `description` | TEXT | nullable | Main searchable content for FTS + embedding |
| `price` | NUMERIC(12,2) | NOT NULL, default 0 | Product price (VND) |
| `metadata_` | JSONB | default `{}` | Keyword tags, category, etc. (FR-003) |
| `created_at` | TIMESTAMPTZ | auto-set | Ingestion timestamp |
| `updated_at` | TIMESTAMPTZ | auto-set, on-update | Last modification |

**Validation rules**:
- `sku` must be unique — enforced by DB UNIQUE constraint + SQLAlchemy index
- `price >= 0` — validated at application layer before insert
- `description` is nullable to support stub products without content yet

---

### `text_embeddings` — Embedding governance (FR-014)

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| `id` | UUID | PK, default uuid4 | Chunk identifier (used as `chunk_id` in citations) |
| `source_id` | UUID | FK → products.id, NOT NULL | Parent product |
| `source_type` | VARCHAR(50) | NOT NULL | e.g., `product_description` |
| `embedding` | vector(1024) | NOT NULL | Fixed-dimension vector (bge-m3) |
| `model_name` | VARCHAR(100) | NOT NULL | e.g., `bge-m3` — prevents mixing incompatible embeddings |
| `model_version` | VARCHAR(50) | nullable | Pinned version string if available |
| `created_at` | TIMESTAMPTZ | auto-set | When embedding was generated |

**Indexes**:
- HNSW index on `embedding` with `vector_cosine_ops` — supports `<=>` operator
  (cosine distance) for fast approximate nearest-neighbour search

**Validation rules**:
- `model_name` must match the environment's configured embedding model; mixing
  models (e.g., bge-m3 and nomic-embed-text) produces incorrect distances
- Dimension must be 1024 — enforced by `Vector(1024)` type definition

---

### `RAGResult` — Pipeline output (in-memory Pydantic, not persisted)

This is the structured return type of `answer_with_rag()`. It is returned to
the caller (API handler or eval script) and is **not** stored directly as a
database row. Citation metadata is stored in `conversation_messages.source_chunk_ids`.

| Field | Type | Description |
|-------|------|-------------|
| `answer` | str | Generated answer text, or DECLINE_MESSAGE |
| `declined` | bool | True if confidence guard fired |
| `citations` | list[dict] | `[{product_id, chunk_id, sku, name}]` per chunk (FR-011) |
| `best_similarity` | float | Highest cosine similarity before compression (FR-010) |
| `rrf_scores` | list[float] | RRF scores for all pre-compression chunks |
| `query_category` | Literal | `short` / `long` / `ambiguous` (FR-015) |
| `top_k_used` | int | Adaptive TopK value: 5, 15, or 20 (FR-009) |
| `model_used` | str | LiteLLM model alias used for generation |
| `escalation_flag` | bool | Whether premium model was selected (Article XII) |
| `chunks_before_compression` | int | Chunk count before compression (SC-005 metric) |
| `chunks_after_compression` | int | Chunk count after compression (SC-005 metric) |

---

### `NormalizedQuery` — Query rewrite output (FR-004)

Pydantic model returned by `AIGateway.normalize_query()` — not persisted directly.

| Field | Type | Description |
|-------|------|-------------|
| `canonical` | str | Cleaned query in original language |
| `detected_language` | str | Language code: `vi`, `en`, or `mixed` |
| `intent` | str | One of: `INFO_QUERY`, `PRICING`, `COMPARISON`, `COMPLAINT`, `NEGOTIATION`, `AVAILABILITY`, `OTHER` |
| `extracted_keywords` | list[str] | Up to 10 FTS-enrichment keywords |

---

### `semantic_cache` — L1/L2 cache (existing, referenced)

Existing table supporting the Semantic Cache layer. The RAG pipeline checks this
cache before calling the retrieval stack (see System Flow in spec).

| Field | Type | Description |
|-------|------|-------------|
| `query_hash` | VARCHAR(64) PK | SHA-256 of canonical query (L1 — O(1) lookup) |
| `query_text` | TEXT | Canonical query text |
| `response` | TEXT | Cached answer |
| `embedding` | vector(1024) | Query embedding for L2 vector search |
| `model_name` | VARCHAR | Model used to generate the cached response |
| `citations` | JSONB | Article IX citations associated with the response |
| `similarity_score` | FLOAT | Similarity score at time of cache write |
| `created_at` | TIMESTAMPTZ | Cache entry creation time |

---

### `EvaluationRecord` — Gold dataset entry (file-based, not DB)

Stored in `tests/eval/gold_dataset.json`. Structure per item:

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Unique ID, e.g., `vn_001`, `sme_001` |
| `query` | str | Vietnamese or English product query |
| `expected_keywords` | list[str] | Keywords that must appear in the answer (Tier 1 check) |
| `category` | str | One of `short`, `long`, `ambiguous` (for Article XII efficiency testing) |
| `difficulty` | str | `easy` or `hard` (for escalation assertion in Article XII) |

---

## State Transitions

### RAG Pipeline Flow

```
Query received
    │
    ▼
classify_query()  ──→  short (TopK=5) / long (TopK=15) / ambiguous (TopK=20)
    │
    ▼
AIGateway.embed()  ─── Exception? ──→  RAGResult(declined=True, "Service unavailable")
    │
    ▼
Truncate >500 words
    │
    ▼
hybrid_search_rrf()  ──→  merged list sorted by RRF score
    │
    ▼
compute best_similarity
    │
    ▼
compress_context()  ──→  dedup → score<0.5 → near-dup(>80%)
    │
    ▼
best_similarity < 0.7                    chunks_after == 0
    OR                          ──→  RAGResult(declined=True, DECLINE_MESSAGE)
chunks_after_compression == 0
    │
    ▼ (passed)
Build context + citations
    │
    ▼
AIGateway.complete()
    │
    ▼
RAGResult(declined=False, answer, citations, ...)
```

---

## Indexes & Performance

| Table | Index | Type | Purpose |
|-------|-------|------|---------|
| `text_embeddings` | `idx_text_embeddings_embedding` | HNSW cosine | Fast ANN vector search |
| `products` | `sku` | B-tree unique | Fast SKU lookup |
| `semantic_cache` | `idx_semantic_cache_embedding` | HNSW cosine | L2 semantic cache lookup |
| `semantic_cache` | `query_hash` | B-tree PK | L1 exact-match cache lookup |

FTS indexes are implicit — `to_tsvector('simple', ...)` is computed at query
time. A GIN index on a tsvector column would improve FTS performance for large
datasets (deferred to post-Week 2 optimisation).
