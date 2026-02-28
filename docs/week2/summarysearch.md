# Hybrid Search Implementation — Techniques Audit & Trade-off Analysis

> Source spec: `docs/report/2.5.md`  
> Codebase snapshot: `2026-02-28`

---

## 1. Implementation Status Matrix

| Technique | Spec (2.5.md) | Status | Location |
|---|---|---|---|
| Hybrid Search (Vector + FTS) | ✅ Required | **Implemented** | `services/rag/retrieval.py` |
| RRF fusion (k=60) | ✅ Required | **Implemented** | `services/rag/retrieval.py` |
| HNSW index on embeddings | ✅ Required | **Implemented** | `models/schema.py`, migration `87456b64657a` |
| GIN index on FTS | ✅ Required | **Implemented** | migration `e9f1c3add123` |
| Over-fetching (2× top_k) | ✅ Recommended | **Implemented** | `retrieval.py:fetch_k = top_k * 2` |
| Parameterized queries (no injection) | ✅ Required | **Implemented** | `retrieval.py` uses dict params |
| Timeout protection per search branch | Not in spec | **Implemented (bonus)** | `asyncio.wait_for(..., timeout=10.0)` |
| `unaccent` extension | ✅ Required | **NOT Implemented** | — |
| `'vietnamese'` FTS config | ✅ Required | **NOT Implemented** | Uses `'simple'` instead |
| Generated Column `content_tsvector` | ✅ Required | **NOT Implemented** | FTS computed inline per query |
| Weighted Sum fusion (alternative) | ✅ Mentioned | **NOT Implemented** | Only RRF |
| Reranking (cross-encoder / API) | ✅ Required (spec) | **NOT Implemented** | — |
| Model Escalation by intent | ✅ Required (spec) | **NOT Implemented** | `escalation_flag` stored but always `False` |
| LangGraph StateGraph orchestration | ✅ Required (spec) | **NOT Implemented** | Direct pipeline in `pipeline.py` |
| HITL (Human-in-the-Loop) | ✅ Required (spec) | **NOT Implemented** | — |
| Telegram Interface | ✅ Required (spec) | **NOT Implemented** | — |
| Semantic Cache L1 (SHA-256 hash) | ✅ Required (spec) | **Implemented** | `services/semantic_cache.py` |
| Semantic Cache L2 (pgvector cosine) | ✅ Required (spec) | **Implemented** | `services/semantic_cache.py` |
| Context Compression (dedup + near-dup) | ✅ Required (spec) | **Implemented** | `services/rag/compression.py` |
| Context Compression via LLM summarization | ✅ Required (spec) | **NOT Implemented** | Only dedup/near-dup, no LLM summarization |
| Adaptive TopK (short/long/ambiguous) | ✅ Required (spec) | **Implemented** | `services/rag/query.py` |
| Query Normalization (NormalizedQuery) | ✅ Required (spec) | **Implemented** | `services/ai.py:normalize_query` |
| Embedding governance (model+version in DB) | ✅ Required (spec) | **Implemented** | `models/schema.py:TextEmbedding` |
| Confidence scoring / guard | ✅ Required (spec) | **Implemented** | `pipeline.py:CONFIDENCE_THRESHOLD` |

---

## 2. Deep Dive: What We Implemented vs. What the Report Prescribes

### 2.1 Vietnamese FTS: `'simple'` vs. `'vietnamese'` + `unaccent`

**What spec says:**
```sql
to_tsvector('vietnamese', unaccent(coalesce(name, '')))
```

**What codebase does:**
```python
to_tsvector('simple', COALESCE(p.name, '') || ' ' || COALESCE(p.description, ''))
```

**Trade-off:**

| | `'simple'` (current) | `'vietnamese'` + `unaccent` (spec) |
|---|---|---|
| **Setup** | Zero config, works out of the box | Requires `CREATE EXTENSION unaccent` + custom FTS config |
| **Vietnamese diacritics** | Case-insensitive but accent-sensitive — `"điện thoại"` ≠ `"dien thoai"` | Strips diacritics → same token → higher recall for SMS-style queries |
| **SKU / code matching** | ✅ Exact match preserved | ✅ Exact match preserved |
| **English queries** | ✅ Works fine | ✅ Works fine |
| **Recall for typo-heavy queries** | ❌ Miss accent variations | ✅ Handles `dien thoai` → `điện thoại` |
| **Risk** | Real Vietnamese users often type without accents on mobile | None — only adds recall |

**Decision rationale (current):** Deferred to avoid infra complexity at dev stage. Using `'simple'` is a valid MVP choice but **costs recall** on accent-stripped Vietnamese input — a real SME risk.

---

### 2.2 Generated Column vs. Inline `to_tsvector`

**What spec says:**
```sql
ALTER TABLE products ADD COLUMN content_tsvector tsvector
GENERATED ALWAYS AS (
    setweight(to_tsvector('vietnamese', unaccent(coalesce(name, ''))), 'A') || ...
) STORED;
CREATE INDEX idx_products_fts ON products USING GIN(content_tsvector);
```

**What codebase does:**  
GIN index is an expression index:
```sql
CREATE INDEX idx_products_fts ON agent_v1.products
USING gin(to_tsvector('simple', COALESCE(name,'') || ' ' || COALESCE(description,'')));
```
FTS query also recomputes `to_tsvector(...)` inline per row.

**Trade-off:**

| | Expression index + inline (current) | Generated Column STORED (spec) |
|---|---|---|
| **Query speed** | Postgres may or may not use expression index | ✅ Index always used, pre-computed |
| **Write overhead** | Only index maintained | Small: column updated on INSERT/UPDATE |
| **Schema migration** | Simpler | Requires ALTER TABLE + reindex |
| **`setweight` (A/B scoring)** | ❌ Not used — name and description equal weight | ✅ Name tokens ranked higher than description |
| **Maintainability** | FTS logic scattered across SQL strings | FTS logic centralized in schema |

**Decision rationale (current):** Simpler to ship. Main cost: loss of `setweight` → name-matches rank equal to description-matches → lower precision for short product-name queries.

---

### 2.3 RRF vs. Weighted Sum

**What spec says:** Both are mentioned; RRF is "most popular in 2026."

**What codebase does:** Only RRF.

**Trade-off:**

| | RRF (current) | Weighted Sum (not implemented) |
|---|---|---|
| **Ground truth needed** | ❌ No | ✅ Yes — requires labeled eval set |
| **Score normalization** | ❌ Not needed (rank-based) | ✅ Required (BM25 → [0,1]) |
| **Robustness to outlier scores** | ✅ Rank-based, robust | ❌ Sensitive to score distribution |
| **Tunability** | Fixed k=60 | Can weight by domain (e.g., 70% FTS for SKUs) |
| **When to switch** | Good default | Better when eval data proves one source dominates |

**Decision rationale (current):** Correct choice for MVP. Weighted Sum should be revisited after building evaluation dataset (spec `002-vietnamese-rag-eval`).

---

### 2.4 Model Escalation — Schema Without Logic

**What spec says:**
> If intent ∈ {COMPLAINT, NEGOTIATION} → escalate to premium model immediately.

**What codebase does:**  
`escalation_flag` is declared in `RAGResult` and stored as `False` everywhere. `normalize_query()` correctly detects intent (COMPLAINT, NEGOTIATION, etc.) but the pipeline **never reads it** to change the model.

**Trade-off / Risk:**
- All queries use `economy-chat` regardless of sensitivity.
- A COMPLAINT or NEGOTIATION routed to a weak local model risks poor/harmful responses.
- **Fix:** Read `normalized.intent` after step 2 in `pipeline.py`, set `model = "premium-chat"` and `escalation_flag = True` for sensitive intents.

---

### 2.5 Context Compression — Dedup Only, No LLM Summarization

**What spec says:** Deduplicate + summarize repetitive info + remove low-signal text.

**What codebase does:**
- ✅ Step 1: Exact dedup by `description` text
- ✅ Step 2: Filter chunks with `vector_score < 0.25`
- ✅ Step 3: Near-duplicate removal via `SequenceMatcher`
- ❌ LLM summarization of repetitive/overlapping chunks → not implemented

**Trade-off:**
- LLM summarization adds latency (~1–2 extra LLM calls) and cost.
- For an SME product catalog (short, structured descriptions), dedup alone achieves 20–40% token reduction without LLM overhead.
- LLM summarization matters most for long-form documents (PDFs, articles) — less critical for current product catalog use case.

---

### 2.6 Reranking — Not Implemented

**What spec says:** Adaptive reranking after retrieval. Dev: local CrossEncoder. Prod: Cohere/Jina/Voyage async API.

**What codebase does:** RRF is used as the sole ranking signal. No reranker step.

**Trade-off:**

| | No reranker (current) | CrossEncoder local (dev) | Cohere/Jina (prod) |
|---|---|---|---|
| **Latency** | ✅ Fastest | ❌ Blocks event loop if not offloaded | ✅ Async API |
| **Precision** | Lower — RRF purely rank-based | ✅ Semantic cross-attention | ✅ Best |
| **Cost** | $0 | $0 | API cost per request |
| **Complexity** | ✅ Simple | Medium | Low (just an API call) |

**Impact:** For SME product catalog (tens to hundreds of products), RRF without reranking may be sufficient. Reranker is high-value when catalog size exceeds thousands of items with overlapping descriptions.

---

### 2.7 LangGraph — Not Implemented

**What spec says:** LangGraph StateGraph is mandatory for agent orchestration.

**What codebase does:** `pipeline.py` is a linear async function — classify → cache → embed → retrieve → compress → generate.

**Trade-off:**

| | Linear pipeline (current) | LangGraph StateGraph |
|---|---|---|
| **HITL support** | ❌ Not possible | ✅ `interrupt_before` nodes |
| **Branching logic** | ❌ If/else in one function | ✅ Conditional edges |
| **State persistence** | ❌ Stateless per request | ✅ Checkpointer (DB-backed) |
| **Debuggability** | Medium | ✅ LangSmith traces per node |
| **Complexity** | ✅ Simple | Medium |

**Impact:** Without LangGraph, HITL (checkout, order confirmation, refunds) is blocked. This is the single biggest architectural gap for production readiness.

---

## 3. Summary of Gaps Prioritized by Risk

| Priority | Gap | Risk | Effort |
|---|---|---|---|
| 🔴 High | Model escalation logic (intent → model switch) | Wrong model for complaints/negotiations | Low — 5 lines in `pipeline.py` |
| 🔴 High | LangGraph migration | HITL blocked; no stateful sessions | High |
| 🟡 Medium | `unaccent` + `'vietnamese'` FTS config | Recall loss on mobile Vietnamese input | Low — 1 migration + config |
| 🟡 Medium | Reranker (local CrossEncoder) | Precision degrades at scale | Medium |
| 🟡 Medium | Generated column `content_tsvector` | Suboptimal FTS precision (no setweight) | Low — 1 migration |
| 🟢 Low | Weighted Sum fusion | RRF sufficient without labeled data | High (needs eval dataset first) |
| 🟢 Low | LLM context summarization | Token overhead acceptable for product catalog | Medium |
| 🟢 Low | Telegram interface | Not needed for API-first testing | Medium |
