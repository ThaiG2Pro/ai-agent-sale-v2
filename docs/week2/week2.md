# Week 2 — Developer Knowledge Base
> **Audience:** New developers joining to debug, refactor, or extend the system.  
> **Purpose:** Architecture trace, dependency chains, logic flows, setup guide.

---

## Table of Contents
1. [What this system is](#1-what-this-system-is)
2. [Repository layout](#2-repository-layout)
3. [Tech stack and constraints](#3-tech-stack-and-constraints)
4. [Database schema](#4-database-schema)
5. [Configuration and model tiers](#5-configuration-and-model-tiers)
6. [Ingestion pipeline](#6-ingestion-pipeline)
7. [Query / RAG pipeline (the core)](#7-query--rag-pipeline-the-core)
8. [Semantic cache (L1 / L2)](#8-semantic-cache-l1--l2)
9. [Hybrid retrieval and RRF](#9-hybrid-retrieval-and-rrf)
10. [Context compression](#10-context-compression)
11. [API layer](#11-api-layer)
12. [CLI tool](#12-cli-tool)
13. [Observability (logfire + Phoenix)](#13-observability-logfire--phoenix)
14. [Migrations](#14-migrations)
15. [Testing](#15-testing)
16. [Setup from scratch](#16-setup-from-scratch)
17. [Common debug traces](#17-common-debug-traces)
18. [Critical gotchas](#18-critical-gotchas)

---

## 1. What this system is

An **SME-ready AI Sales Agent** (Vietnamese market, 2026) built for zero-cost-first, local-first operation.

- Users ask product questions via CLI or HTTP POST.
- The system answers using **Retrieval-Augmented Generation (RAG)**:
  - Retrieves relevant product chunks from PostgreSQL (vector + full-text search).
  - Compresses context, guards confidence, generates an answer via a local LLM (Ollama).
- All computation is **offline-capable**: no cloud dependency required.

---

## 2. Repository layout

```
ai-agent-sale-v2/
├── api/                        # FastAPI web layer
│   ├── main.py                 # App factory, lifespan, middleware wiring
│   ├── dependencies.py         # Shared: verify_admin_key()
│   ├── middleware/
│   │   └── middleware.py       # TimingMiddleware, exception handlers
│   └── routes/
│       ├── query.py            # POST /query  — public RAG endpoint
│       ├── admin.py            # POST /admin/rag/{ingest,search,stats}
│       └── health.py           # GET /health
├── cli/
│   └── rag_admin.py            # Typer CLI: ingest / search / query / stats
├── core/
│   ├── config.py               # pydantic-settings — all env vars
│   ├── ai_config.py            # LiteLLM Router model list
│   └── logging.py              # logfire setup + Phoenix gRPC wiring
├── models/
│   └── schema.py               # SQLAlchemy ORM models (schema: agent_v1)
├── services/
│   ├── database.py             # AsyncEngine + AsyncSessionLocal
│   ├── ai.py                   # AIGateway (complete/embed/normalize_query)
│   ├── semantic_cache.py       # L1 (hash) + L2 (vector) cache
│   └── rag/
│       ├── pipeline.py         # answer_with_rag() — main orchestrator
│       ├── ingest.py           # ingest_product_text() — product ingestion
│       ├── retrieval.py        # hybrid_search_rrf() + search_products()
│       ├── compression.py      # compress_context()
│       ├── query.py            # classify_query() + compute_adaptive_topk()
│       └── constants.py        # Algorithm constants + prompts
├── scripts/
│   ├── ingest_catalog.py       # Bulk ingest from JSON catalog
│   ├── product-catalog.json    # 19 Vietnamese tech products
│   ├── seed_bulk.py            # LLM-generated seed products (dev only)
│   └── cleanup_db.py           # Dev: delete all data
├── migrations/                 # Alembic migration files
├── specs/
│   └── 002-vietnamese-rag-eval/
│       └── contracts/          # Canonical Pydantic type contracts
├── tests/
│   ├── conftest.py             # Redirects tests to ai_agent_test DB
│   ├── unit/
│   └── integration/
├── docker-compose.yml          # PostgreSQL 17 + pgvector + Phoenix
├── .env                        # Local secrets (not committed)
├── .env.example                # Template
└── pyproject.toml              # uv dependencies + ruff config
```

### Key `__init__.py` re-exports
`services/rag/__init__.py` re-exports `answer_with_rag`, `ingest_product_text`,
`search_products` so callers use `from services.rag import ...` without knowing
internal submodule structure.

---

## 3. Tech stack and constraints

| Layer | Technology | Why |
|-------|-----------|-----|
| Runtime | Python 3.13+ | Latest, async-native |
| Package manager | `uv` | Fast, lockfile-based |
| HTTP API | FastAPI + Uvicorn | Async, OpenAPI out-of-box |
| LLM gateway | LiteLLM Router | Vendor-agnostic, retries built-in |
| Local LLMs | Ollama | Zero-cost offline inference |
| Embeddings | bge-m3 (1024-dim) | Best multilingual (vi/en), local |
| Database | PostgreSQL 17 + pgvector 0.8 | Single DB; vector + FTS + JSON |
| ORM | SQLAlchemy 2.0 async | No lazy loading, explicit sessions |
| Observability | logfire + OpenTelemetry | Structured spans, Phoenix traces |
| CLI | Typer + Rich | Type-safe, pretty output |
| IDs | UUIDv7 (uuid-utils) | Time-ordered, client-side gen |

**Hard constraints (never violate):**
- ❌ No Redis, no Celery, no Kubernetes
- ❌ No direct SDK imports (openai, anthropic) — always go through LiteLLM
- ❌ No ORM lazy loading — always use `selectinload` or explicit queries
- ❌ No blocking I/O in async functions — CPU-bound tasks via `run_in_executor`
- ❌ No multiple databases — PostgreSQL is the only store

---

## 4. Database schema

All tables live in the `agent_v1` PostgreSQL schema.

### Tables

```
agent_v1
├── products                    # Core product catalog
│   ├── id          UUID PK     # UUIDv7 — time-ordered
│   ├── sku         VARCHAR(50) # Unique, indexed
│   ├── name        VARCHAR(255)
│   ├── description TEXT
│   ├── price       NUMERIC(12,2)
│   ├── metadata    JSONB       # Enriched: specs, category, keywords, summary
│   ├── content_tsvector        # GENERATED ALWAYS AS stored column
│   │                           # = setweight(to_tsvector(unaccent(name)), 'A')
│   │                           # || setweight(to_tsvector(unaccent(desc)), 'B')
│   ├── created_at  TIMESTAMPTZ
│   └── updated_at  TIMESTAMPTZ
│
├── text_embeddings             # Vector embeddings (1 per product currently)
│   ├── id          UUID PK     # UUIDv7
│   ├── source_id   UUID FK → products.id
│   ├── source_type VARCHAR(50) # e.g. "product_description"
│   ├── embedding   VECTOR(1024)# bge-m3 embedding
│   ├── model_name  VARCHAR(100)# e.g. "ollama/bge-m3"
│   ├── model_version VARCHAR(50)
│   ├── keywords    JSONB       # list[str] for FTS enrichment
│   └── created_at  TIMESTAMPTZ
│
├── semantic_cache              # L1/L2 response cache
│   ├── query_hash  VARCHAR(64) PK  # SHA256 of canonical query (L1 key)
│   ├── query_text  TEXT
│   ├── response    TEXT
│   ├── embedding   VECTOR(1024)# query embedding (L2 search)
│   ├── model_name  VARCHAR(100)# embedding model used
│   ├── citations   JSONB       # [{product_id, chunk_id, sku, name}]
│   ├── similarity_score FLOAT
│   └── created_at  TIMESTAMPTZ
│
├── conversation_sessions       # Future: multi-turn conversations
├── conversation_messages       # Future: message history
├── sales_signals               # Future: intent/budget tracking
└── model_traces                # Observability: tokens, cost, guard signals
    ├── id          UUID PK
    ├── message_id  UUID FK (nullable — Week 5 link)
    ├── model_name  VARCHAR
    ├── prompt/completion/total_tokens INT
    ├── latency_ms  FLOAT
    ├── cost        NUMERIC(10,6)
    └── metadata    JSONB       # guard_decision, best_similarity, similarity_gap, top_k_used
```

### Key indexes
- `text_embeddings.embedding`: HNSW index (`vector_cosine_ops`) — fast ANN search
- `semantic_cache.embedding`: HNSW index — fast L2 cache lookup
- `products.sku`: B-tree unique index
- `products.content_tsvector`: GIN index — fast FTS
- `sales_signals.signal_type`: B-tree index

### Vietnamese FTS (migration `f8a2c1d3e5b7`)
The `content_tsvector` column uses `agent_v1.immutable_unaccent()` — a thin
IMMUTABLE wrapper around `public.unaccent()`. This is required because
PostgreSQL disallows STABLE functions in GENERATED columns.

Effect: `"dien thoai"` matches `"điện thoại"` — users can search without typing
Vietnamese diacritics.

---

## 5. Configuration and model tiers

### `core/config.py` — `Settings` (pydantic-settings)
All configuration is loaded from environment variables or `.env`.

| Variable | Default | Purpose |
|----------|---------|---------|
| `DB_*` | localhost/5432/ai_agent | PostgreSQL connection |
| `X_ADMIN_KEY` | `dev-secret-key` | Admin endpoint auth |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API |
| `LIGHT_CHAT_MODEL` | `ollama/qwen3:0.6b` | Fast cheap tasks |
| `CHAT_MODEL` | `ollama/qwen3-1.7b` | Main chat model |
| `POWERFUL_CHAT_MODEL` | `ollama/deepseel-r1:1.5b` | Escalation |
| `EMBED_MODEL` | `ollama/bge-m3` | Embeddings |
| `EMBED_DIMENSION` | `1024` | Must match bge-m3 |
| `LOGFIRE_TOKEN` | (empty) | Logfire cloud (optional) |
| `OTLP_ENDPOINT` | `http://localhost:4317` | Phoenix gRPC |

### `core/ai_config.py` — LiteLLM Router model list

```
Model alias       → Ollama model         → Use case
─────────────────────────────────────────────────────
light-chat        → qwen3:0.6b (522MB)  → keyword extraction (fast, isolated)
economy-chat      → qwen3-1.7b (1.1GB)  → normalize_query + RAG answer (same model!)
economy-embedding → bge-m3 (1.2GB)      → all embeddings
premium-local-chat→ deepseel-r1:1.5b    → escalation
premium-chat      → groq/llama-3.1-70b  → cloud fallback
```

**Why normalize + answer use the same model (`economy-chat`):**  
Ollama loads one model at a time into VRAM. Switching models mid-request causes
VRAM thrashing and OOM. Using the same model for both `normalize_query` and
`generate_answer` avoids any swap.

---

## 6. Ingestion pipeline

**Entry point:** `services/rag/ingest.py` → `ingest_product_text(db, name, sku, description, price, metadata)`

### Full call chain

```
ingest_product_text(db, name, sku, description, price)
│
├── SELECT Product WHERE sku = ? → skip if exists (idempotent)
│
├── INSERT Product(id=uuid7(), sku, name, description, price)
│   db.flush()  ← gets DB ID without commit
│
├── AIGateway.embed(embed_text, model="economy-embedding")
│   └── ai_router.aembedding("ollama/bge-m3", input=[text])
│       └── returns list[list[float]] (1024 dims)
│   SEQUENTIAL — bge-m3 must finish before next model loads
│
├── enrich_metadata_async(description, name, sku)
│   └── ai_router.acompletion("economy-chat", response_format=ProductMetadata)
│       └── returns ProductMetadata(specs, keywords, seo_summary, category, intent)
│   SEQUENTIAL — after embed (Ollama single-model-at-a-time rule)
│
├── validate_metadata_vs_source(description, enriched_metadata)
│   ├── Check: ≥40% of spec VALUES appear in original text (language-agnostic)
│   ├── Keywords: logged diagnostically but NOT used for validity gate
│   │   (qwen3-1.7b returns English keywords for Vietnamese text → false negatives)
│   └── returns bool: use enriched metadata or fall back to minimal
│
├── extract_keywords_structured(description, name)
│   └── ai_router.acompletion("light-chat", response_format=KeywordExtraction, timeout=45)
│       └── returns list[str] (3–10 keywords)
│   TIMEOUT=45s — qwen3:0.6b can hang indefinitely (known Ollama bug)
│
└── INSERT TextEmbedding(id=uuid7(), source_id=product.id, embedding=vector,
                          model_name=EMBED_MODEL, keywords=keywords)
    db.commit()
```

### Bulk ingest (`scripts/ingest_catalog.py`)

```
ingest_catalog.py ingest
│
├── load_catalog_file(path)         # Read + validate JSON via CatalogFile Pydantic
│
├── asyncio.Semaphore(embed_concurrency=1)
│                                   # 1 = sequential (safe for local Ollama)
│                                   # Increase only if using cloud APIs
│
└── asyncio.as_completed(tasks)     # Progress bar, per-product sessions
    └── ingest_single_product(product, semaphore)
        └── new AsyncSessionLocal() per product
            └── ingest_product_text(...)
```

**Catalog JSON structure** (`scripts/product-catalog.json`):
```json
{
  "catalog": [
    {
      "sku": "IPHONE-15-PRO-MAX-256",
      "name": "iPhone 15 Pro Max 256GB",
      "category": "Điện thoại",
      "subcategory": "Apple iPhone",
      "price": 29990000,
      "currency": "VND",
      "description": "...(Vietnamese product description)...",
      "intent": "B2C",
      "specifications": {"chip": "A17 Pro", ...}
    }
  ],
  "metadata": { ... }
}
```

---

## 7. Query / RAG pipeline (the core)

**Entry point:** `services/rag/pipeline.py` → `answer_with_rag(db, query, model="economy-chat")`

### 14-step flow

```
answer_with_rag(db, query)
│
│ Step 1 — Initial TopK (word-count heuristic)
├── classify_query(query) → "short" | "long" | "ambiguous"
│   ├── ≤10 words  → "short"
│   ├── 11–25 words → "long"
│   └── >25 words + no action verb/proper noun → "ambiguous"
├── compute_adaptive_topk(query) → 5 | 15 | 20
│
│ Step 2 — Query normalization (LLM)
├── AIGateway.normalize_query(query)
│   ├── Heuristic pre-check: len<3 or digit-only → return is_valid=False (zero LLM cost)
│   ├── ai_router.acompletion("economy-chat", response_format=NormalizedQuery)
│   └── returns NormalizedQuery(canonical, language, intent, keywords, is_valid)
│
│ Step 2a — Spam/gibberish guard
├── if not normalized.is_valid:
│       return RAGResult(answer="Vui lòng đặt câu hỏi...", declined=True)
│
│ Step 2b — Intent-driven TopK override
├── compute_adaptive_topk(query, intent=normalized.intent)
│   ├── PRICING / INFO_QUERY → 5    (one product, specific fact)
│   ├── COMPARISON           → 10   (two products, cross-match)
│   └── other                → word-count fallback (5/15/20)
│
│ Step 3 — L1 cache (exact match, O(1))
├── get_l1_cache(db, canonical_query)
│   ├── canonicalize: strip + lowercase
│   ├── SHA256 hash → SELECT semantic_cache WHERE query_hash = ?
│   └── HIT → return cached RAGResult instantly (model_used="cache")
│
│ Step 4 — Embed query
├── AIGateway.embed(query, "economy-embedding")  ← uses ORIGINAL query, not canonical
│   Why original: canonical is LLM-generated (temperature>0) → non-deterministic
│   vectors → inconsistent L2 cache hits if using canonical
│   └── returns vector[1024]
│
│ Step 5 — L2 cache (semantic match, pgvector)
├── get_l2_cache(db, query_vector, threshold=0.95)
│   └── SELECT ... WHERE 1-cosine_dist > 0.95 ORDER BY similarity DESC LIMIT 1
│   HIT → return cached RAGResult (model_used="cache")
│
│ Step 6 — FTS query truncation (>500 words)
│
│ Step 7 — Hybrid retrieval
├── hybrid_search_rrf(db, query_vector, fts_query_text, top_k)
│   ├── Vector search:  SELECT ... ORDER BY embedding <=> query_vec LIMIT top_k*2
│   ├── FTS search:     SELECT ... WHERE content_tsvector @@ plainto_tsquery(
│   │                               'simple', immutable_unaccent(query)) LIMIT top_k*2
│   └── RRF merge:      score[chunk] += 1/(60 + rank)  for each source
│                        final ranking by combined RRF score
│
│ Step 8 — Similarity gap scoring
├── best_similarity = max(vector_score for chunk in retrieved)
├── similarity_gap  = score[rank1] - score[rank2]  (0 if single result)
│   Large gap  (>0.15) = clear winner, high confidence
│   Small gap  (<0.01) = ambiguous, may need reranking
│
│ Step 9 — Context compression
├── compress_context(retrieved, best_similarity=best_similarity)
│   ├── Step 1: Deduplicate by description text
│   ├── Step 2: Relative threshold filter:
│   │           effective_threshold = max(0.25, best_similarity × 0.65)
│   │           Keep only chunks with vector_score ≥ effective_threshold
│   └── Step 3: Near-duplicate removal (SequenceMatcher ratio > 0.80)
│
│ Step 10 — Confidence guard
├── if best_similarity < 0.45 OR chunks_after_compression == 0:
│       write model_trace (guard_decision="REJECTED")
│       return RAGResult(answer=DECLINE_MESSAGE, declined=True)
│
│ Step 11 — Build context and citations
├── Format each chunk: "[SKU] Name\nGiá: X VND\nDescription"
├── citations: [{product_id, chunk_id, sku, name}]
│
│ Step 12 — LLM answer generation (timed)
├── AIGateway.complete(messages, model="economy-chat")
│   └── ANSWER_SYSTEM_PROMPT + context + customer question
│
│ Step 13 — model_trace write (best-effort, non-blocking)
├── INSERT ModelTrace(tokens, cost, latency_ms, metadata={guard, similarity, gap})
│
│ Step 14 — Cache write (best-effort, never blocks response)
└── set_cache(db, canonical_query, response, embedding, EMBED_MODEL, citations)
    └── db.merge(SemanticCache) + commit
```

### `RAGResult` fields (what callers receive)

| Field | Type | Meaning |
|-------|------|---------|
| `answer` | str | Answer text or decline message |
| `declined` | bool | True = confidence guard fired |
| `citations` | list[dict] | Source products used |
| `best_similarity` | float | Top cosine score (0–1) |
| `similarity_gap` | float | top1 − top2 scores |
| `rrf_scores` | list[float] | All RRF scores before compression |
| `query_category` | str | short/long/ambiguous |
| `top_k_used` | int | Adaptive TopK value |
| `model_used` | str | "economy-chat" or "cache" |
| `escalation_flag` | bool | Premium model used? |
| `chunks_before_compression` | int | Retrieved count |
| `chunks_after_compression` | int | After filtering |

---

## 8. Semantic cache (L1 / L2)

**File:** `services/semantic_cache.py`

### L1 — Exact hash match
```
canonicalize_query(query)   → strip + lowercase
generate_query_hash(query)  → sha256(canonical).hexdigest()

get_l1_cache(db, query):
    SELECT response, citations FROM semantic_cache
    WHERE query_hash = SHA256(canonical)
    AND   model_name = EMBED_MODEL
```
Cost: 0 tokens, ~0ms DB lookup. Identical queries (even from different users)
hit L1 immediately after the first answer is cached.

### L2 — Semantic vector match
```
get_l2_cache(db, query_embedding, threshold=0.95):
    SELECT response, citations, (1-embedding<=>query_embedding) AS similarity
    FROM semantic_cache
    WHERE (1 - embedding <=> query_embedding) > 0.95
    AND   model_name = EMBED_MODEL
    ORDER BY similarity DESC
    LIMIT 1
```
Cost: 1 embedding call (bge-m3), ~3–5ms pgvector HNSW lookup.
Catches near-identical phrasing: "iPhone price?" vs "How much iPhone costs?"

### Cache write
```
set_cache(db, canonical_query, response, embedding, EMBED_MODEL, citations):
    db.merge(SemanticCache(...))   # Upsert: update if query_hash exists
    db.commit()
```

**Cache invalidation:** Currently TTL-based only (no explicit invalidation on
product updates). When product data changes, old cache entries may serve stale
answers until manually cleared via `scripts/cleanup_db.py`.

---

## 9. Hybrid retrieval and RRF

**File:** `services/rag/retrieval.py`

### Why hybrid (not just vector)?
- Vector alone misses keyword-exact matches ("iPhone 15 Pro Max 256GB")
- FTS alone misses semantic matches ("cái điện thoại mới của Apple" → iPhone)
- RRF fusion gives the best of both worlds without needing separate weights

### RRF algorithm
```
For each retrieved chunk from each source (vector, FTS):
    score[chunk_id] += 1 / (k=60 + rank)

Final ranking: sort by combined score descending, return top_k
```
`k=60` is the standard RRF constant (from the 2009 Cormack paper).
Chunks present in both sources get two additive contributions — natural
precision boost.

### FTS query path
```sql
WHERE p.content_tsvector @@ plainto_tsquery(
    'simple',
    agent_v1.immutable_unaccent(:qtext)  -- strips Vietnamese diacritics
)
ORDER BY ts_rank(content_tsvector, ...) DESC
LIMIT :top_k * 2
```

The `setweight(A/B)` in the stored column means name matches rank higher than
description matches in `ts_rank`.

### `search_products()` (direct search, no full RAG pipeline)
Used by admin search endpoint and CLI `rag-admin search`. Calls
`hybrid_search_rrf` directly after embedding the query. Returns results with a
normalized `score` key (= `rrf_score`) for display compatibility.

---

## 10. Context compression

**File:** `services/rag/compression.py`

### Why compression matters
- bge-m3 retrieves up to 20 chunks. Without compression, every chunk goes to
  the LLM context window → high token cost, noisy answers.
- Target: 20–40% token reduction on typical queries.

### Algorithm

```
compress_context(chunks, best_similarity):
│
│ Step 1: Deduplication (exact text)
│   seen = set()
│   keep chunks where description not in seen
│
│ Step 2: Relative confidence filter
│   effective_threshold = max(0.25, best_similarity × 0.65)
│
│   Example: best_similarity = 0.79
│     → effective_threshold = max(0.25, 0.79×0.65) = max(0.25, 0.51) = 0.51
│     → drops all chunks with vector_score < 0.51
│     → typically 60–80% of chunks removed
│
│   Example: best_similarity = 0.30 (low confidence query)
│     → effective_threshold = max(0.25, 0.30×0.65) = max(0.25, 0.195) = 0.25
│     → floor prevents over-filtering when retrieval confidence is low
│
└── Step 3: Near-duplicate removal (SequenceMatcher)
    effective_threshold = 0.80 (NEAR_DUP_THRESHOLD)
    Preserve highest rrf_score variant when two chunks are >80% similar
```

**Key insight:** The absolute COMPRESSION_SCORE_THRESHOLD (0.25) acts as a
safety floor. The actual filter is dynamic based on retrieval quality.

---

## 11. API layer

**Files:** `api/main.py`, `api/routes/`, `api/middleware/`, `api/dependencies.py`

### Startup sequence (`lifespan`)
```
1. setup_logging()            — logfire console + Phoenix gRPC span processor
2. SQLAlchemy engine created  — asyncpg pool
3. DB connectivity check      — SELECT 1
4. SQLAlchemyInstrumentor()   — OTel traces for SQL queries
5. FastAPIInstrumentor()      — OTel traces for HTTP requests
6. asyncio.create_task(_warmup_model())  — pre-load economy-chat in background
   → Sends "hi" to Ollama → model loads into VRAM
   → Eliminates 30–90s cold start on first real query
7. yield  — application runs
8. engine.dispose()           — clean shutdown
```

### Routes

| Method | Path | Auth | Handler |
|--------|------|------|---------|
| GET | `/` | None | Root info |
| GET | `/health` | None | DB ping + status |
| POST | `/query` | None | `answer_with_rag()` |
| POST | `/admin/rag/ingest` | X-Admin-Key | `ingest_product_text()` |
| POST | `/admin/rag/search` | X-Admin-Key | `search_products()` |
| POST | `/admin/rag/stats` | X-Admin-Key | Product/embedding counts |

### Request/response flow (query)
```
POST /query {"query": "iPhone giá bao nhiêu?", "model": "economy-chat"}
  → TimingMiddleware (start timer)
  → verify_admin_key [NOT required for /query]
  → get_db() → AsyncSession injected via Depends
  → answer_with_rag(db, query, model)
  → QueryResponse(answer, declined, citations, best_similarity, ...)
  → TimingMiddleware (add X-Process-Time header, logfire span)
```

### Admin key
Set `X_ADMIN_KEY` in `.env`. Send as `X-Admin-Key: <value>` header.
Default dev value: `dev-secret-key`.

---

## 12. CLI tool

**File:** `cli/rag_admin.py`  
**Run:** `uv run python -m cli.rag_admin <command>`  
**Shortcut:** `rag-admin <command>` (after `uv run` installs entry point)

### Commands

```bash
# Ingest a product (local DB, no server needed)
uv run python -m cli.rag_admin ingest \
  --name "iPhone 15" --sku "IPH15-001" \
  --description "..." --price 25000000 --local

# Ask a question (local DB, no server needed)
uv run python -m cli.rag_admin query "MacBook Pro giá bao nhiêu?" --local

# Semantic search
uv run python -m cli.rag_admin search "laptop Apple" --local --top-k 5

# Stats
uv run python -m cli.rag_admin stats --local

# All commands also work via API (requires running server):
uv run python -m cli.rag_admin query "..." --api-url http://localhost:8000
```

### `--local` vs API mode
- `--local`: directly imports `AsyncSessionLocal` and service functions, runs
  in the same process. No server needed. Used for dev/debug.
- API mode (default): sends HTTP requests to the running FastAPI server.
  Timeouts: ingest=300s, query=180s (Ollama serializes model loads).

### Output panels (Rich)
- 🤖 RAG Answer — the generated answer
- 📚 Citations — source products used
- 📊 RAG Metrics — similarity, topK, model, compression
- Execution Info — mode (local/api)

---

## 13. Observability (logfire + Phoenix)

**File:** `core/logging.py`

### Setup chain
```
setup_logging()
│
├── _build_phoenix_processor()
│   └── OTLPSpanExporter(endpoint="http://localhost:4317", insecure=True)
│       └── BatchSpanProcessor(exporter)
│       # gRPC, NOT HTTP — HTTP (port 4318) resets connections with Python 3.13
│       # insecure=True = no Authorization headers (Phoenix has auth disabled)
│
├── logfire.configure(
│     token=None,                        # no cloud unless LOGFIRE_TOKEN set
│     send_to_logfire=False,             # local dev: console only
│     console=ConsoleOptions(verbose=True),   # colored span tree in terminal
│     additional_span_processors=[phoenix_processor]  # → Phoenix UI
│   )
│
├── logging.basicConfig(handlers=[LogfireLoggingHandler()])
│   # Bridges Python logging → logfire spans (unifies all log sources)
│
└── Suppress OTLP HTTP error logger (CRITICAL level)
    # Prevents logfire cloud export 401 errors from polluting output
```

### What you see in the terminal
```
15:03:51.446 Observability initialized (Phoenix: http://localhost:4317)
15:03:51.476 RAG pipeline started: MacBook Pro gia bao nhieu?
             │ Query normalized: lang=vi, intent=PRICING, is_valid=True
             │ TopK adjusted by intent: intent=PRICING, top_k=5
15:03:53.550 L1 cache miss: query_hash=9c47a0bf
             │ L1 lookup: hash=9c47a0bf, model=ollama/bge-m3, found=False
             │ L2 lookup: model=ollama/bge-m3, threshold=0.95, found=False
             │ Retrieved 5 chunks, best_similarity=0.7841, similarity_gap=0.1230
             │ Compression: 5->2 chunks (60% reduction)
             │ model_trace written: guard=ACCEPTED, gap=0.1230, tokens=347
             │ Cache write completed for embedding model
```

### Phoenix UI
- Access: `http://localhost:6006/playground`
- Receives the same spans via gRPC (port 4317)
- Shows full trace tree per request — useful for latency breakdown

### LangSmith / Logfire cloud (optional)
Set `LOGFIRE_TOKEN=<token>` in `.env` — console output is disabled, cloud
export is enabled. For LangGraph traces, set `LANGSMITH_API_KEY`.

---

## 14. Migrations

**Tool:** Alembic (`alembic.ini` → `migrations/`)

### Migration chain
```
87456b64657a  foundation_v1
    └── dcd5e99fdf41  add_sales_signals_and_model_traces
        └── 46344f09af22  add_gin_index_for_fts
            └── 05a8b68c724f  add_keywords_column_to_text_embeddings
                └── e9f1c3add123  add_gin_index_products_fts
                    └── f8a2c1d3e5b7  add_unaccent_vietnamese_fts (HEAD)
```

### Commands
```bash
# Apply all migrations
uv run alembic upgrade head

# Check current revision
uv run alembic current

# Generate new migration from ORM changes
uv run alembic revision --autogenerate -m "description"

# Rollback one step
uv run alembic downgrade -1
```

### Why `agent_v1` schema?
Namespace isolation. Multiple schema versions can coexist in the same DB,
allowing gradual migrations without breaking live data.

---

## 15. Testing

**Test DB:** `ai_agent_test` (separate from `ai_agent` dev DB)

`tests/conftest.py` force-sets `os.environ["DB_NAME"] = "ai_agent_test"` **before
any imports** — this overrides `.env` via pydantic-settings priority.

### Test isolation
1. Session-scoped fixture `_setup_test_database` creates `ai_agent_test` if
   missing and runs `alembic upgrade head`.
2. Per-test `db_session` fixture DELETEs all rows in
   `text_embeddings`, `products`, `semantic_cache` after each test.

### Running tests
```bash
# All tests (requires Ollama running for integration tests)
uv run pytest

# Skip slow integration tests
uv run pytest tests/unit/

# Specific test
uv run pytest tests/integration/test_rag.py::test_rag_ingestion_and_search -v
```

### Test files
| File | Purpose |
|------|---------|
| `tests/unit/test_rag_helpers.py` | classify_query, compute_adaptive_topk, compress_context |
| `tests/unit/test_health.py` | GET /health endpoint |
| `tests/integration/test_rag.py` | Full ingest + search + cache flow |
| `tests/integration/test_hybrid_rrf.py` | RRF merge logic |
| `tests/integration/test_ai_offline.py` | AIGateway with mocked Ollama |
| `tests/integration/test_search_latency.py` | Search latency < 500ms SLA |

---

## 16. Setup from scratch

### Prerequisites
- Docker + Docker Compose
- [Ollama](https://ollama.ai/) installed locally
- `uv` package manager (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Python 3.13+

### Step 1 — Start infrastructure
```bash
# Create DB password secret
mkdir -p secrets
echo "your_db_password" > secrets/db_password.txt

# Start PostgreSQL 17 + pgvector + Phoenix
docker compose up -d

# Verify
docker compose ps
```

### Step 2 — Install dependencies
```bash
uv sync
```

### Step 3 — Configure environment
```bash
cp .env.example .env
# Edit .env:
#   DB_PASSWORD=your_db_password
#   X_ADMIN_KEY=your-admin-key
```

### Step 4 — Pull Ollama models
```bash
ollama pull bge-m3              # Embeddings (1.2GB)
ollama pull qwen3:0.6b          # Light chat (522MB)
ollama pull qwen3-1.7b          # Main chat (1.1GB)
ollama pull deepseel-r1:1.5b    # Escalation (1.1GB)
```

### Step 5 — Run migrations
```bash
uv run alembic upgrade head
```

### Step 6 — Ingest product catalog
```bash
uv run python scripts/ingest_catalog.py ingest
# ~10 min for 19 products on local Ollama (sequential, one model at a time)
```

### Step 7 — Start the API server
```bash
uv run uvicorn api.main:app --reload
# Server: http://localhost:8000
# Docs:   http://localhost:8000/docs
```

### Step 8 — Test a query
```bash
# Via CLI (no server needed)
uv run python -m cli.rag_admin query "iPhone 15 Pro Max giá bao nhiêu?" --local

# Via API
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{"query": "MacBook Pro gia bao nhieu?"}'
```

### Step 9 — Check Phoenix traces
Open `http://localhost:6006/playground` — spans appear after first query.

---

## 17. Common debug traces

### Trace: cache hit (fastest path)
```
RAG pipeline started
│ Query normalized
│ TopK adjusted: PRICING → 5
L1 cache miss: hash=xxxx
│ L1 lookup: found=False
│ AI Embedding started (bge-m3)
│ AI Embedding finished (0.3s)
L2 cache hit for query: similarity=0.9820
→ Return immediately, model_used="cache"
```

### Trace: full pipeline (new query)
```
RAG pipeline started
│ Query normalized: intent=PRICING
│ TopK adjusted: 15 → 5
L1 cache miss
│ AI Embedding finished (0.3s)
│ L2 cache miss
│ Retrieved 5 chunks, best_similarity=0.7841
│ Compression: 5→2 chunks (60% reduction)
│ AI Completion started (economy-chat)
│ AI Completion finished (7s)
│ model_trace written: guard=ACCEPTED
│ Cache write completed
→ Return answer
```

### Trace: confidence guard fired
```
RAG pipeline started
│ Query normalized: intent=OTHER
│ Retrieved 3 chunks, best_similarity=0.3921
│ Confidence guard fired: best_sim=0.3921, chunks_after=0
│ model_trace written: guard=REJECTED
→ Return DECLINE_MESSAGE, declined=True
```

### Trace: spam guard
```
RAG pipeline started
│ normalize_query: rejected by heuristic (too short / digit-only)
→ Return "Vui lòng đặt câu hỏi liên quan...", declined=True
```

---

## 18. Critical gotchas

### G1: Ollama one-model-at-a-time
**Problem:** `asyncio.gather(embed_task, enrich_task)` → both tasks try to load
different Ollama models simultaneously → OOM crash.

**Rule:** All Ollama calls must be sequential. No `asyncio.gather` across
different model aliases. The ingest pipeline explicitly sequences:
`embed() → enrich() → keywords()`.

### G2: UUIDv7 for all IDs
All `id` columns use `uuid7()` from `uuid-utils`. This is time-ordered
(lexicographic sort = creation order), client-side generated (no DB round-trip
needed before insert), and random-suffix ensures security.
**Never use `uuid4()`** for new records — it breaks ordering guarantees.

### G3: Embedding dimension must match
`EMBED_DIMENSION=1024` must match the model in use (bge-m3 = 1024).
If you change the embedding model, all existing vectors are invalid.
**Never mix embeddings from different models in the same database.**
The `model_name` field in `text_embeddings` and `semantic_cache` tracks this.

### G4: Cache uses ORIGINAL query, not canonical
L2 cache and the `set_cache()` call both use `query` (original), not
`canonical_query` (LLM output). Reason: `normalize_query` uses temperature>0,
making `canonical_query` non-deterministic across calls. Embedding the same
original query always produces the same vector → consistent cache hits.

### G5: FTS needs `immutable_unaccent()`
`unaccent()` is STABLE in PostgreSQL, which is insufficient for GENERATED
columns and function indexes (require IMMUTABLE). Migration `f8a2c1d3e5b7`
creates `agent_v1.immutable_unaccent()` as a thin IMMUTABLE SQL wrapper.
**Do not drop this function** — it will break the entire FTS column.

### G6: `is_valid` gate is specs-only
`validate_metadata_vs_source()` checks only `technical_specs` values (not
keywords) for validity. Reason: `qwen3-1.7b` outputs English keywords for
Vietnamese text, causing false negatives. The spec values (numbers, model
names) appear verbatim in both languages and are a reliable signal.

### G7: Keyword extraction timeout=45s
`extract_keywords_structured()` uses `light-chat` (qwen3:0.6b) with
`timeout=45`. Without this, Ollama can hang for 900+ seconds on some inputs
(observed with KEYBOARD-MECH-001 product). Products still ingest successfully
with empty keywords (FTS degrades gracefully).

### G8: OTLP HTTP (port 4318) doesn't work
Phoenix's OTLP HTTP endpoint causes `ConnectionResetError(104)` with Python
3.13 + urllib3. Always use gRPC (port 4317) with `insecure=True`. See
`_build_phoenix_processor()` in `core/logging.py`.

### G9: Test DB isolation
`tests/conftest.py` must be the **first** module loaded in any test session —
it sets `os.environ["DB_NAME"]` before any app imports. Never import
`core.config` before `conftest.py` runs. The `pytest.ini_options.pythonpath`
ensures this order.

### G10: `model_trace.message_id` is nullable
`ModelTrace.message_id` is nullable because `ConversationMessage` isn't
implemented yet (Week 5). The FK constraint exists but no message is linked
during RAG queries. Do not make it non-nullable until conversations are built.
