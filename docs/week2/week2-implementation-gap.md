# 🔍 WEEK 2 — IMPLEMENTATION GAP ANALYSIS

> **Mục đích:** So sánh từng kỹ thuật trong `week2-techniques-overview.md` với trạng thái thực tế trong codebase.
> **Ngày audit lần đầu:** 2026-02-28  
> **Cập nhật lần 2:** 2026-03-01 — sau sprint RAG optimization (TopK, Compression, Observability)
> **Codebase revision:** HEAD (`002-vietnamese-rag-eval` branch)

---

## 🔄 THAY ĐỔI KỂ TỪ AUDIT TRƯỚC (2026-02-28 → 2026-03-01)

> Sprint này tập trung tối ưu pipeline sau khi phân tích 7 query logs thực tế.

| Vấn đề | Trước | Sau | File |
|---|---|---|---|
| **Adaptive TopK** | Word-count only (short ≤5 words) | Intent-driven override: PRICING/INFO_QUERY→5, COMPARISON→10; short threshold 5→10 words | `services/rag/query.py` |
| **Context Compression** | Absolute threshold 0.25 (không lọc gì) | Relative threshold: `max(0.25, best_similarity×0.65)` | `services/rag/compression.py` |
| **Confidence Threshold** | 0.35 | **0.45** (calibrated cho bge-m3 on-topic scores 0.49–1.0) | `services/rag/constants.py` |
| **Metadata validation** | `is_valid = keywords_valid AND specs_valid` (always False cho VI text) | `is_valid = specs_valid` only (keywords diagnostic-only) | `services/rag/ingest.py` |
| **Keyword extraction** | Không có timeout → có thể hang 900s | `timeout=45` hard cap | `services/rag/ingest.py` |
| **normalize_query model** | `light-chat` (qwen3:0.6b) | `economy-chat` (qwen3-1.7b) — tránh Ollama model swap với generate_answer | `services/ai.py` |
| **Phoenix OTLP** | Broken — HTTP 4318 reset connections | Wired via gRPC 4317, `insecure=True`, `additional_span_processors` | `core/logging.py` |
| **Model tiers** | `economy-chat` = qwen3-4b-q6 (3.3GB, OOM) | `economy-chat` = qwen3-1.7b (1.1GB) | `.env`, `core/config.py` |
| **Re-ingest catalog** | 1/19 products enriched | 16/19 products enriched (193s total) | `scripts/ingest_catalog.py` |

---

## 📊 TỔNG QUAN NHANH

| Trạng thái | Số lượng kỹ thuật |
|---|---|
| ✅ Đã implement đúng spec | 18 (+2 so với audit trước) |
| ⚠️ Implement theo cách khác (alternative) | 7 (unchanged) |
| ❌ Chưa implement | 5 (unchanged) |

---

## 🧭 MỤC LỤC

1. [Stack cốt lõi (lặp lại xuyên suốt)](#1-stack-cốt-lõi)
2. [Task 2.3 — Metadata Enrichment](#2-task-23--metadata-enrichment)
3. [Task 2.4 — Query Normalization](#3-task-24--query-normalization)
4. [Task 2.5 — Hybrid Search](#4-task-25--hybrid-search)
5. [Task 2.6 — Gold Dataset](#5-task-26--gold-dataset)
6. [Task 2.7 — RAG Flow v1](#6-task-27--rag-flow-v1)
7. [Task 2.8 — Evaluation CLI Runner](#7-task-28--evaluation-cli-runner)
8. [Task 2.9 — Adaptive TopK](#8-task-29--adaptive-topk)
9. [Task 2.10 — Similarity Gap Scoring](#9-task-210--similarity-gap-scoring)
10. [Task 2.11 — Citation Metadata Mapping](#10-task-211--citation-metadata-mapping)
11. [Task 2.12 — Context Compression](#11-task-212--context-compression)
12. [Task 2.13 — Confidence Threshold Guard](#12-task-213--confidence-threshold-guard)
13. [Bảng tổng hợp quyết định](#13-bảng-tổng-hợp-quyết-định)

---

## 1. Stack cốt lõi

### 1.1 LiteLLM + Pydantic V2 Structured Output

**✅ ĐÃ IMPLEMENT**

| File | Nội dung |
|---|---|
| `services/ai.py` | `AIGateway.complete()`, `AIGateway.embed()`, `AIGateway.normalize_query()` — tất cả qua `ai_router.acompletion()` |
| `services/ai.py` | `NormalizedQuery`, `KeywordExtraction`, `ProductMetadata` — Pydantic models với `response_format=` |
| `core/ai_config.py` | LiteLLM Router với `economy-chat`, `economy-embedding`, `premium-chat` |

**Cách implement:**
```python
# services/ai.py — đúng pattern, không dùng SDK trực tiếp
response = await ai_router.acompletion(
    model="economy-chat",
    messages=messages,
    response_format=NormalizedQuery,  # Pydantic model trực tiếp
    temperature=0,
)
validated = NormalizedQuery.model_validate_json(content)
```

**Đánh giá:** Đúng 100% spec. `response_format` trỏ Pydantic, `model_validate_json()` sau đó, không có regex.

---

### 1.2 PostgreSQL 17 + pgvector + JSONB

**✅ ĐÃ IMPLEMENT (đầy đủ hạ tầng, một số tối ưu thiếu)**

| Tính năng | Trạng thái | File |
|---|---|---|
| pgvector + HNSW index | ✅ | `models/schema.py` — `TextEmbedding.embedding`, `SemanticCache.embedding` |
| JSONB | ✅ | `Product.metadata_`, `SemanticCache.citations`, `ConversationMessage.source_chunk_ids` |
| GIN index (FTS) | ✅ | Migration `e9f1c3add123` → replaced by `f8a2c1d3e5b7` |
| unaccent extension | ✅ | Migration `f8a2c1d3e5b7` — `CREATE EXTENSION unaccent` + `immutable_unaccent()` |
| Generated Column tsvector | ✅ | Migration `f8a2c1d3e5b7` — `content_tsvector GENERATED ALWAYS AS` với setweight A/B |
| VACUUM cải tiến | ✅ | Tự động — PostgreSQL 17 mặc định |

**✅ IMPLEMENTED — unaccent + Generated Column (Migration `f8a2c1d3e5b7`):**
```sql
-- immutable wrapper (required because unaccent() is STABLE, not IMMUTABLE)
CREATE FUNCTION agent_v1.immutable_unaccent(text) RETURNS text
  LANGUAGE sql IMMUTABLE PARALLEL SAFE STRICT AS
  $$ SELECT public.unaccent('unaccent', $1) $$;

-- stored tsvector with setweight A/B
ALTER TABLE agent_v1.products ADD COLUMN content_tsvector tsvector
  GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', immutable_unaccent(coalesce(name,''))), 'A') ||
    setweight(to_tsvector('simple', immutable_unaccent(coalesce(description,''))), 'B')
  ) STORED;

-- retrieval.py now uses:
-- p.content_tsvector @@ plainto_tsquery('simple', agent_v1.immutable_unaccent(:qtext))
```

**Note:** `'simple'` (không phải `'vietnamese'`) là lựa chọn đúng — tiếng Việt không cần stemming, `unaccent` xử lý dấu. `setweight A/B` boost tên sản phẩm hơn mô tả.

---

### 1.3 Async Python (asyncio / AnyIO)

**⚠️ PARTIAL — asyncio có, AnyIO chỉ dùng một chỗ**

| Kỹ thuật | Trạng thái | File |
|---|---|---|
| `async/await` xuyên suốt | ✅ | Tất cả services |
| `asyncio.wait_for()` timeout | ✅ | `retrieval.py` — 10s timeout cho cả vector và FTS search |
| `anyio.to_thread.run_sync` | ⚠️ | Chỉ dùng trong `tier1_eval.py` cho stdin, không dùng cho CPU offload |
| `anyio.CapacityLimiter` backpressure | ❌ | Không implement |
| Offload local reranker | N/A | Chưa có reranker local |

**Đánh giá:** Ổn cho week 2. `CapacityLimiter` cần khi tải cao (week 6+). Không có CPU-bound task cần offload hiện tại.

---

### 1.4 Small Language Models (SLM) — 3-Tier Strategy

**✅ ĐÃ IMPLEMENT — mở rộng thành 3 tiers**

> **⚡ Cập nhật 2026-03-01:** Model tiers thay đổi sau OOM incident với qwen3-4b-q6 (3.3GB).

| Model | Config | Dùng cho | Size |
|---|---|---|---|
| `ollama/qwen3:0.6b` | `light-chat` | **Keyword extraction only** (isolated, timeout=45s) | 522 MB |
| `ollama/qwen3-1.7b` | `economy-chat` | **normalize_query + generate_answer** (cùng model để tránh swap) | 1.1 GB |
| `ollama/deepseel-r1:1.5b` | `premium-local-chat` | Complex reasoning, escalation local | 1.1 GB |
| `groq/llama-3.1-70b-versatile` | `premium-chat` | Cloud fallback | Cloud |
| `ollama/bge-m3` | `economy-embedding` | Embedding (1024 dim) | 1.2 GB |

**Routing logic (Model Escalation):**
- Keyword extraction → `light-chat` (qwen3:0.6b) — isolated, timeout=45s
- normalize_query + generate_answer → **`economy-chat` (qwen3-1.7b)** — cùng 1 model tránh Ollama swap
- COMPLAINT/NEGOTIATION intent → `premium-local-chat` (deepseel-r1:1.5b) [Week 5]
- Cloud fallback → `premium-chat` (Groq)

**Critical: Tại sao normalize_query không dùng light-chat?**  
Ollama load/unload model giữa 2 call liên tiếp gây OOM khi cả hai active cùng thời điểm. Giữ `normalize_query` + `generate_answer` trên `economy-chat` (qwen3-1.7b) = chỉ 1 model resident. Keyword extraction dùng `light-chat` vì nó là task riêng biệt (ingest, không phải query time).

---

## 2. Task 2.3 — Metadata Enrichment

### 2.3.1 Pydantic Schema + LiteLLM Extraction

**✅ ĐÃ IMPLEMENT**

```python
# services/ai.py — ProductMetadata schema
class ProductMetadata(BaseModel):
    product_id: str
    technical_specs: dict[str, Any]
    keywords: list[str]  # min=3, max=10
    seo_summary: str     # max=100 chars
    category: str
    intent: Literal["commercial", "consumer"]

# services/rag/ingest.py — enrich_metadata_async()
response = await ai_router.acompletion(
    model="economy-chat",
    messages=messages,
    response_format=ProductMetadata,
    temperature=0,
)
enriched = ProductMetadata.model_validate_json(content)
```

**Khác biệt:** Spec có trường `keywords: List[str] = Field(..., min_length=5)` — codebase dùng `min_length=3`. Thực tế thấp hơn spec một chút.

### 2.3.2 Generator-Critic Pattern (Hallucination Detection)

**✅ ĐÃ IMPLEMENT (simplified, updated 2026-03-01)**

```python
# services/rag/ingest.py — validate_metadata_vs_source()
# - spec values phải xuất hiện trong original text (specs_valid)
# - keywords extracted KHÔNG gate validity — chỉ diagnostic/logged
# is_valid = specs_valid  (keywords failure = warning only, not block)
# Fallback to ProductMetadata.minimal() nếu fail
```

**Thay đổi quan trọng:** Trước đây logic là `is_valid = keywords_valid AND specs_valid`. Vì keyword model (qwen3:0.6b) trích từ khóa tiếng Anh cho text tiếng Việt, `keywords_valid` luôn False → 0/19 products enriched. **Fix:** `is_valid = specs_valid` only.

- `specs_valid`: `≥ 60%` technical_specs values xuất hiện trong text gốc → sensible, ổn định
- `keywords_valid`: keywords từ LLM match text gốc → diagnostic only (log warning nếu fail)

**Keyword extraction timeout:**
```python
# Timeout=45s hard cap (qwen3:0.6b hang incident: KEYBOARD-MECH-001 = 900s)
response = await ai_router.acompletion(..., timeout=45)
# On timeout → keywords = [], không block ingest
```

**Trade-off vs Spec:** Spec đề xuất dùng LLM thứ hai (LLM-as-critic). Implementation hiện tại dùng **string matching Python** (không tốn thêm LLM call). Nhanh hơn, rẻ hơn, nhưng ít chính xác hơn với paraphrase.

**Kết quả thực tế:** 16/19 products enriched sau fix (3 còn `category=unknown`: MOUSE, KEYBOARD, HEADPHONE — English spec không match VI text).

### 2.3.3 JSONB Storage

**✅ ĐÃ IMPLEMENT** — `Product.metadata_` kiểu `JSONB` lưu toàn bộ `ProductMetadata.model_dump()`.

### 2.3.4 Contextual Chunking / Retrieval Nuggets

**❌ CHƯA IMPLEMENT**

Hiện tại mỗi product = 1 chunk (1 embedding). Spec đề xuất tạo "retrieval nuggets" (SKU, tên, dòng máy) đính kèm mỗi chunk. Chưa implement vì dataset hiện tại nhỏ và 1 product = 1 description.

**Trade-off nếu implement:** Tăng số embedding records, tăng recall cho multi-aspect queries. Cần khi dataset lớn hoặc description dài.

### 2.3.5 Semantic Chunking / Late Chunking

**❌ CHƯA IMPLEMENT** — Không cần thiết với dataset hiện tại (description ngắn < 500 chars).

### 2.3.6 Prompt Engineering (XML tags cho small model)

**⚠️ PARTIAL** — System prompt dùng numbered list (`1. technical_specs: ...`), không dùng XML tags (`<Specs>`). Đủ cho Qwen3-4B.

---

## 3. Task 2.4 — Query Normalization

### 3.1 NormalizedQuery Pydantic Model

**✅ ĐÃ IMPLEMENT — đầy đủ với `is_valid` guard**

```python
# services/ai.py
class NormalizedQuery(BaseModel):
    canonical: str           # cleaned query
    detected_language: str   # 'vi' / 'en'
    intent: str              # INFO_QUERY | PRICING | COMPARISON | COMPLAINT | NEGOTIATION | AVAILABILITY | OTHER
    extracted_keywords: list[str]  # up to 10 keywords cho FTS
    is_valid: bool           # False nếu spam/gibberish (len<3 hoặc digit-only)
```

**is_valid guard trong `normalize_query()`:**
- Heuristic pre-check trước khi gọi LLM: `len < 3` hoặc `isdigit()` → `is_valid=False` ngay, không tốn token
- LLM kết quả: field `is_valid` trong Pydantic response
- Pipeline step 2a: nếu `is_valid=False` → return `declined=True` ngay lập tức

**⚡ Model routing (cập nhật 2026-03-01):** `normalize_query()` dùng **`economy-chat` (qwen3-1.7b)**, KHÔNG dùng `light-chat`.  
Lý do: tránh Ollama model swap giữa normalize (step 2) và generate (step 12) — cả hai đều dùng `economy-chat` nên model stays resident. Xem Section 1.4 chi tiết.

### 3.2 Selective Normalization (Heuristic pre-check)

**✅ ĐÃ IMPLEMENT — heuristic trong `normalize_query()` trước LLM call**

Heuristic check: nếu `len(query.strip()) < 3` hoặc `query.strip().isdigit()` → trả về `is_valid=False` ngay (0 token, 0 LLM call). Simple English queries vẫn qua LLM vì `qwen3:0.6b` đủ nhanh.

### 3.3 Multi-turn Query (LangGraph State)

**❌ CHƯA IMPLEMENT** — Week 3. `AgentState` với `messages` history chưa có.

### 3.4 Bảo mật Prompt Injection

**⚠️ PARTIAL** — Dùng role separation (System/User), không có explicit delimiters (`<<<USER_INPUT>>>`).

---

## 4. Task 2.5 — Hybrid Search

### 4.1 RRF Algorithm

**✅ ĐÃ IMPLEMENT — chuẩn production**

```python
# services/rag/retrieval.py — hybrid_search_rrf()
# RRF_K = 60 (constants.py)
# Over-fetch: fetch_k = top_k * 2
# Python-side merge (không phải DB CTE)
for rank, row in enumerate(vector_rows):
    scores[cid] += 1.0 / (RRF_K + rank)
for rank, row in enumerate(fts_rows):
    scores[cid] += 1.0 / (RRF_K + rank)
```

**Khác biệt vs spec:** Spec đề xuất dùng **CTE trong PostgreSQL** (fusion tại DB). Codebase dùng **Python-side merge** sau 2 query riêng biệt. 

**Trade-off:**
- Python merge: Dễ debug, linh hoạt, không bị vendor lock SQL
- DB CTE: 1 round-trip thay vì 2, tốt hơn khi dataset lớn (10k+ products)
- **Cho SME scale hiện tại (< 1k products): Python merge là OK**

### 4.2 FTS: `unaccent` + stored `content_tsvector`

**✅ ĐÃ IMPLEMENT (Migration `f8a2c1d3e5b7`)**

```sql
-- Stored generated column (faster than on-the-fly):
content_tsvector GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', immutable_unaccent(coalesce(name,''))), 'A') ||
    setweight(to_tsvector('simple', immutable_unaccent(coalesce(description,''))), 'B')
) STORED;

-- retrieval.py query:
p.content_tsvector @@ plainto_tsquery('simple', agent_v1.immutable_unaccent(:qtext))
```

**"áo thun" query → `ao thun`** — dấu được normalize, FTS match thành công. `setweight A/B` boost tên sản phẩm (A) hơn mô tả (B).

### 4.3 Timeout Protection

**✅ BONUS — không có trong spec** — `asyncio.wait_for(..., timeout=10.0)` cho cả vector và FTS search. Tốt.

### 4.4 Weighted Sum (alternative ranking)

**❌ KHÔNG IMPLEMENT** — Chỉ dùng RRF. Đúng theo spec 2026.

---

## 5. Task 2.6 — Gold Dataset

### 5.1 Format và số lượng

**⚠️ ALTERNATIVE IMPLEMENTATION**

| Tiêu chí | Spec | Thực tế |
|---|---|---|
| Số lượng | ≥10 | **20** ✅ |
| Ngôn ngữ | Tiếng Việt chính | Bilingual (EN + VI, ~50/50) |
| Format | JSON triplet (query, context, expected_answer) | JSON nhưng **không có `context` và `expected_answer`** |
| Fields | `id`, `query`, `context`, `expected_answer`, `metadata` | `id`, `query`, `category`, `intent`, `expected_keywords`, `complexity`, `difficulty`, `language` |

**Thực tế gold_dataset.json:**
```json
{
  "id": "sme_011",
  "query": "Giá sản phẩm widget cao cấp là bao nhiêu?",
  "category": "pricing",
  "intent": "INFO_QUERY",
  "expected_keywords": ["giá", "widget", "cao cấp"],
  "complexity": "easy",
  "difficulty": "easy",
  "language": "vi"
}
```

**Trade-off:** Không có `context` và `expected_answer` nghĩa là evaluation chỉ dựa trên **keyword presence** (Tier 1), không đánh giá được **Faithfulness** hay **Answer Relevancy** theo RAGAs metrics. Đây là simplified evaluation phù hợp với "custom script" approach.

**Nếu muốn reimplementation:** Thêm `context` (product descriptions thực tế) + `expected_answer` → unlock Faithfulness/Relevancy checks. Trade-off: Tốn thời gian annotate thủ công.

### 5.2 PII Redaction

**❌ CHƯA IMPLEMENT** — Không có trong dataset (widget data không có PII).

### 5.3 LLM-as-a-judge

**✅ HITL thay thế** — Tier 2 Likert grading (1–5) bởi human. Không dùng LLM judge tự động.

---

## 6. Task 2.7 — RAG Flow v1

**✅ ĐÃ IMPLEMENT — đầy đủ, production-ready**

```
services/rag/pipeline.py — answer_with_rag()

Step 1:  classify_query() → Adaptive TopK (word-count + intent override)
Step 2a: AIGateway.normalize_query() → canonical + intent + fts_query_text
Step 2b: Intent override TopK (PRICING/INFO_QUERY→5, COMPARISON→10)
Step 3:  get_l1_cache() → SHA256 exact match (0 token)
Step 4:  AIGateway.embed() → query_vector
Step 5:  get_l2_cache() → semantic cache (0.95 threshold)
Step 6:  Truncate >500-word FTS queries
Step 7:  hybrid_search_rrf() → retrieved chunks
Step 8:  max(vector_score) → best_similarity + similarity_gap
Step 9:  compress_context() → deduplicated chunks (relative threshold)
Step 10: Confidence Guard (< 0.45 → DECLINE)
Step 11: Build context + citations
Step 12: AIGateway.complete() → answer_text
Step 13: set_cache() → write L1/L2
```

**Thiếu so với spec:**
- `escalation_flag` luôn `False` — chưa có model escalation logic
- Không dùng Jinja2 templates — prompt là string constants
- Không có extractive summarization (Qwen2.5-1.5B) trước LLM

### Jinja2 Templates

**⚠️ ALTERNATIVE** — Dùng Python string constants trong `services/rag/constants.py` (`ANSWER_SYSTEM_PROMPT`).

**Trade-off:**
- String constants: Đơn giản, không dependency mới, đủ cho static prompts
- Jinja2: Cần khi prompt có logic phân nhánh (if/for), ví dụ khác nhau theo `intent` hoặc `language`
- **Hiện tại chưa cần** — một prompt cho tất cả queries

---

## 7. Task 2.8 — Evaluation CLI Runner

**✅ ĐÃ IMPLEMENT — vượt spec**

| Tính năng | Spec | Thực tế |
|---|---|---|
| CLI runner | ✅ | `scripts/tier1_eval.py` |
| Human grading | ✅ | Tier 2 Likert 1–5 với `--skip-tier2` flag |
| Automated scoring | Không có trong spec | **Tier 1**: keyword presence, citation check, confidence guard check |
| `--skip-tier2` (CI mode) | Không có trong spec | ✅ CI-safe mode |
| Export results | Không có trong spec | ✅ `reports/eval_results.json` với aggregate metrics |
| AnyIO non-blocking stdin | ✅ | `anyio.to_thread.run_sync` cho stdin input |

**Thiếu so với spec:** LLM-as-a-judge tự động (Groq). Hiện dùng human.

---

## 8. Task 2.9 — Adaptive TopK

**✅ ĐÃ IMPLEMENT — đúng spec, cập nhật thêm intent override**

> **⚡ Cập nhật 2026-03-01:** Thêm Step 2b intent override + nâng short threshold 5→10 words.

```python
# services/rag/query.py
def classify_query(query: str) -> Literal["short", "long", "ambiguous"]:
    # word_count ≤ 10 → "short"    ← raised từ ≤5 (queries VI 5-10 từ thường là "short")
    # 11 ≤ word_count ≤ 15 → "long"
    # >15 + has_action_verb or has_proper_noun → "long"
    # >15 + no signal → "ambiguous"

def compute_adaptive_topk(query: str, intent: str | None = None) -> int:
    # Base TopK từ word-count:
    base = {"short": 5, "long": 15, "ambiguous": 20}[classify_query(query)]
    # Step 2b: Intent override (sau normalize_query)
    if intent in ("PRICING", "INFO_QUERY"):
        return 5   # focused query → fewer chunks
    if intent == "COMPARISON":
        return 10  # multi-product comparison → more chunks
    return base
```

**Tại sao step 2b (intent override) quan trọng:**  
Pricing query "MacBook giá bao nhiêu?" có 4 từ → `short` → base TopK=5 ✅. Nhưng comparison query "So sánh MacBook và Dell XPS" có 6 từ → `long` → base TopK=15 → intent override COMPARISON → 10. Đúng hơn về mặt semantic.

**Khác biệt vs spec:**
- Spec: từ khóa so sánh ("khác gì", "so với") → TopK 15. Implementation: COMPARISON intent → TopK 10 (tốt hơn)
- Spec: Hard limit = 30. Implementation: max có thể là 20 (ambiguous). Không cần hard limit.

### Comparative Keywords

**✅ COVERED** — `ACTION_VERBS` trong `constants.py` có "so sánh". COMPARISON intent được classify → TopK 10.

### tiktoken Hard Limit

**❌ KHÔNG IMPLEMENT** — tiktoken không được dùng. TopK max = 20, không cần hard limit 30.

### HyDE / Multi-step Retrieval / Knowledge Graphs

**❌ CHƯA IMPLEMENT** — Advanced features, week 3+.

---

## 9. Task 2.10 — Similarity Gap Scoring

**✅ ĐÃ IMPLEMENT — đầy đủ similarity_gap + model_trace**

### Similarity Gap (top_gap = Score_top1 − Score_top2)

**✅ ĐÃ IMPLEMENT — pipeline step 8:**

```python
# pipeline.py — Step 8:
vec_scores = sorted((c["vector_score"] for c in retrieved), reverse=True)
best_similarity = vec_scores[0] if vec_scores else 0.0
similarity_gap = (
    vec_scores[0] - vec_scores[1] if len(vec_scores) >= 2 else best_similarity
)
# Exposed in RAGResult.similarity_gap
```

**Ý nghĩa:** `similarity_gap` lớn → clear winner (1 sản phẩm nổi bật). `similarity_gap` nhỏ → ambiguous (nhiều kết quả gần nhau, cân nhắc rerank). Signal cho dynamic routing (Week 7).

### model_trace Writes

**✅ ĐÃ IMPLEMENT — ghi sau mỗi LLM call và confidence guard:**

```python
# pipeline.py — _write_model_trace() helper (best-effort, never raises)
# Ghi vào model_traces.metadata_ (JSONB):
{
    "guard_decision": "ACCEPTED" | "REJECTED",
    "best_similarity": 0.4321,
    "similarity_gap": 0.1234,
    "top_k_used": 5,
    "query_category": "faq"
}
# Cộng thêm: model_name, prompt_tokens, completion_tokens, latency_ms, cost
```

**Không cần schema migration** — dùng `metadata_` JSONB đã có trong `ModelTrace`.

### model_trace với guard_decision

**✅ ĐÃ IMPLEMENT** — `ACCEPTED`/`REJECTED` được ghi trong cả hai paths:
- Confidence guard fired → trace với `guard_decision=REJECTED`
- Normal completion → trace với `guard_decision=ACCEPTED` + token counts + cost

---

## 10. Task 2.11 — Citation Metadata Mapping

**⚠️ PARTIAL — Basic citations có, substring validation không có**

### Basic Citations

**✅ CÓ** — `RAGResult.citations` là `list[{product_id, chunk_id, sku, name}]`. Được set vào `ConversationMessage.source_chunk_ids` (JSONB).

```python
# pipeline.py step 11:
citations.append({
    "product_id": chunk["id"],
    "chunk_id": chunk["chunk_id"],
    "sku": chunk["sku"],
    "name": chunk["name"],
})
```

### SupportingFact Schema (substring quote validation)

**❌ CHƯA IMPLEMENT**

Spec đề xuất schema phức tạp hơn:
```python
class SupportingFact(BaseModel):
    fact: str
    substring_quote: List[str]  # trích dẫn nguyên văn
    source_id: str
    relevance_score: float      # 0-1

# @field_validator kiểm tra substring_quote có trong original text
```

**Trade-off nếu implement:**
- Pro: Chặn hallucinated citations, tăng Add-to-Cart Conversion 6–12%
- Con: Cần thay đổi LLM prompt + response schema, tăng token usage
- **Khuyến nghị:** Implement khi có customer-facing interface. Week 2 không critical.

### LiteLLM Tag Routing

**❌ CHƯA IMPLEMENT** — `sales-high-priority` tag routing. Hiện tại routing đơn giản theo model alias.

### ABAC / Deep Attestation / Audit Trail

**❌ CHƯA IMPLEMENT** — Week 7+ / Production hardening.

---

## 11. Task 2.12 — Context Compression

**✅ ĐÃ IMPLEMENT — đầy đủ, 3 bước + relative threshold**

> **⚡ Cập nhật 2026-03-01:** Threshold từ absolute 0.25 → relative `max(0.25, best_similarity × 0.65)`.

```python
# services/rag/compression.py — compress_context()
# Step 1: Exact text dedup (set-based O(n))
# Step 2: Low-confidence filter — relative threshold:
#         threshold = max(0.25, best_similarity × 0.65)
#         chunk bị loại nếu vector_score < threshold
# Step 3: Near-duplicate removal (SequenceMatcher > NEAR_DUP_THRESHOLD=0.80)
```

**Tại sao relative threshold tốt hơn absolute 0.25:**

| Scenario | best_similarity | relative threshold | kết quả |
|---|---|---|---|
| On-topic query | 0.70 | `max(0.25, 0.70×0.65)` = **0.455** | Lọc chặt hơn — chỉ giữ chunks tốt nhất |
| Off-topic query | 0.35 | `max(0.25, 0.35×0.65)` = **0.25** | Fall back to floor — giữ tất cả (ít chunks, cần all) |
| Borderline | 0.50 | `max(0.25, 0.50×0.65)` = **0.325** | Trung bình |

Absolute 0.25 = hầu như không lọc gì với bge-m3 (scores thường 0.3–0.7). Relative threshold adaptive theo quality của retrieval.

**So sánh với spec:**

| Phương pháp spec | Codebase | Match? |
|---|---|---|
| ID-based Dedup | Step 1 exact text dedup (description hash via set) | ⚠️ Tương đương nhưng dùng description text, không ProductID |
| Hash-based Dedup | Không dùng MD5/SHA trực tiếp — dùng exact string | ⚠️ Functionally equivalent cho exact match |
| Semantic Dedup (embedding + clustering) | ❌ Không có | ❌ |
| Fuzzy Matching (Levenshtein) | Step 3: SequenceMatcher (LCS ratio) | ✅ Tương đương — SequenceMatcher tốt hơn Levenshtein |

### Small Model Compression (SMC / ACC-RAG)

**❌ CHƯA IMPLEMENT**

Spec đề xuất dùng Qwen2.5-1.5B tóm tắt trước khi gửi GPT-4o. Hiện tại chỉ dedup + filter, không summarize.

**Trade-off:**
- Pro: Giảm 80% token khi context dài
- Con: Thêm 1 LLM call, thêm latency 500ms–1s
- **Breakeven: context > 2,000 tokens**. Hiện tại descriptions ngắn — chưa cần.

### erank formula

**❌ KHÔNG IMPLEMENT** — Quá phức tạp cho SME scope. Bỏ qua.

### Pinned State cho static info

**❌ CHƯA IMPLEMENT** — Thông tin bảo hành, địa chỉ chưa được pin vào system prompt. Gap thực tế cho SME.

---

## 12. Task 2.13 — Confidence Threshold Guard

**⚠️ ALTERNATIVE IMPLEMENTATION — ngưỡng calibrated, đúng về kỹ thuật**

> **⚡ Cập nhật 2026-03-01:** Threshold 0.35 → **0.45** sau calibration với 7 real queries.

### Guard Logic

**✅ CÓ** — Pipeline step 10:
```python
CONFIDENCE_THRESHOLD: float = 0.45  # constants.py (was 0.35 → raised after calibration)

if best_similarity < CONFIDENCE_THRESHOLD or chunks_after == 0:
    return RAGResult(answer=DECLINE_MESSAGE, declined=True, ...)
```

**Khác biệt quan trọng — 0.45 vs 0.7:**

| | Spec | Thực tế |
|---|---|---|
| Ngưỡng | 0.7 | **0.45** |
| Lý do | Generic threshold | Calibrated cho `bge-m3` cross-lingual embeddings |

**Giải thích:** `bge-m3` trả về cosine similarity thấp hơn text-embedding-ada-002. Cross-lingual similarity (VI query ↔ VI product) thường 0.49–0.80 cho on-topic, 0.30–0.44 cho off-topic. Ngưỡng 0.7 = reject on-topic results → hệ thống vô dụng. Ngưỡng 0.45 cân bằng: 7 query thực tế → 6 ACCEPTED, 1 borderline (0.47).

**Calibration data (7 queries thực, 2026-03-01):**
- "MacBook dang co gia bao nhieu?" → best_sim = 0.87 → ACCEPTED ✅
- "laptop gaming" → best_sim = 0.72 → ACCEPTED ✅  
- "tai nghe khong day" → best_sim = 0.69 → ACCEPTED ✅
- Off-topic: "thời tiết hôm nay" → best_sim = 0.31 → REJECTED ✅ (correct)

**Kết luận:** ⚠️ NOT a bug — intentional calibration. Threshold cần re-calibrate khi đổi embedding model.

### Dynamic Threshold per Intent / Channel

**❌ CHƯA IMPLEMENT**

Spec: `float(os.getenv(f"THRESHOLD_{intent.upper()}", 0.75))` — ngưỡng riêng cho từng intent.

**Trade-off nếu implement:**
- Pro: COMPLAINT/NEGOTIATION có thể cần ngưỡng cao hơn (ít hallucinate)
- Con: Cần calibration data cho từng intent
- **Khuyến nghị:** Implement sau khi có đủ production data

### Fallback tinh tế (graduated responses)

**⚠️ PARTIAL** — Chỉ có 1 `DECLINE_MESSAGE` string cho tất cả levels. Spec đề xuất 3 levels (Quick Replies / Related Topics / Human handoff).

### Reranking 2 giai đoạn

**❌ CHƯA IMPLEMENT** — Không có Cross-Encoder. Spec đề xuất: 50–100 candidates → rerank → guard.

### model_trace với guard_decision

**✅ ĐÃ IMPLEMENT** — xem Section 9 bên trên.

---

## 13. Bảng tổng hợp quyết định

### ✅ Đã implement đúng spec

| Kỹ thuật | File | Ghi chú |
|---|---|---|
| LiteLLM + Pydantic response_format | `services/ai.py` | Production-ready |
| HNSW vector index | `models/schema.py` | |
| JSONB metadata storage | `models/schema.py` | |
| GIN FTS index + unaccent + setweight A/B | Migration `f8a2c1d3e5b7` | Stored `content_tsvector` column |
| unaccent extension + `immutable_unaccent()` | Migration `f8a2c1d3e5b7` | Dùng trong generated column + query |
| Hybrid Search + RRF (k=60) | `services/rag/retrieval.py` | Python-side merge |
| Over-fetch (2×) trước RRF | `retrieval.py` `fetch_k = top_k * 2` | |
| Timeout protection (10s) | `retrieval.py` `asyncio.wait_for` | Bonus vs spec |
| Keyword extraction timeout (45s) | `services/rag/ingest.py` | Prevents qwen3:0.6b hang |
| Query Normalization + `is_valid` guard | `services/ai.py` | `economy-chat` model + heuristic pre-check |
| Selective normalization heuristic | `services/ai.py` | `len<3` / `isdigit()` skip LLM |
| Metadata Enrichment + Critic (specs_valid) | `services/rag/ingest.py` | `is_valid = specs_valid` only |
| Adaptive TopK (5/10/15/20) + intent override | `services/rag/query.py` | PRICING→5, COMPARISON→10 |
| Context Compression (3 steps, relative) | `services/rag/compression.py` | `max(0.25, best_sim×0.65)` |
| Confidence Guard → DECLINE | `services/rag/pipeline.py` | Threshold **0.45** (calibrated) |
| Similarity Gap (top1 − top2) | `services/rag/pipeline.py` step 8 | `RAGResult.similarity_gap` |
| model_trace writes (guard+gap+cost) | `services/rag/pipeline.py` | `_write_model_trace()` helper |
| Semantic Cache L1 (SHA256) | `services/semantic_cache.py` | |
| Semantic Cache L2 (vector 0.95) | `services/semantic_cache.py` | |
| Basic Citations (product_id/chunk_id/sku) | `pipeline.py` step 11 | |
| Eval CLI (Tier 1 + Tier 2 HITL) | `scripts/tier1_eval.py` | Vượt spec |
| Gold Dataset (20 queries, bilingual) | `tests/eval/gold_dataset.json` | |
| Observability (Logfire console + Phoenix gRPC) | `core/logging.py` | Phoenix wired via `additional_span_processors` |

### ⚠️ Implement theo cách khác (Alternative)

| Kỹ thuật | Spec | Thực tế | Trade-off |
|---|---|---|---|
| FTS Dictionary | `'vietnamese'` + `unaccent` | `'simple'` + `unaccent` | `'simple'` là đúng — VI không cần stemming; `unaccent` đã xử lý dấu |
| RRF Fusion location | DB CTE (1 round-trip) | Python-side (2 queries) | Python dễ debug; DB CTE tốt hơn cho scale lớn |
| Confidence Threshold | 0.7 | **0.45** | Calibrated cho bge-m3 cross-lingual — đúng về kỹ thuật, khác spec |
| Critic pattern | LLM-based critic | String matching (specs_valid only) | Rẻ hơn; miss paraphrase cases; keywords diagnostic only |
| normalize_query model | `light-chat` (spec không định rõ) | `economy-chat` | Tránh Ollama model swap → prevent OOM |
| Gold dataset format | Triplets (query+context+expected_answer) | keyword-based (query+expected_keywords) | Evaluation đơn giản hơn; không đo Faithfulness/Relevancy |
| Fallback graduated response | 3 levels (0.6/0.4/0.4) | 1 static message | Đủ cho week 2; nâng cấp khi có UX layer |

### ❌ Chưa implement (và trade-off để implement)

| Kỹ thuật | Task | Lý do chưa | Trade-off implement |
|---|---|---|---|
| **Dynamic threshold per intent** | 2.13 | Cần calibration data | Trung bình: thêm ENV vars, cần data |
| **SupportingFact substring validation** | 2.11 | Schema change + prompt change | Cao: tăng token, cần LLM prompt mới. Implement khi có UI. |
| **Small Model Compression (SMC)** | 2.12 | Context hiện tại ngắn | Cao: cần model nhẹ riêng + LLM call. Implement khi context > 2k tokens. |
| **Cross-Encoder Reranking** | 2.7, 2.13 | Không có local model | Cao: cần HuggingFace model hoặc API. Implement ở Week 7. |
| **Pinned State (static info in prompt)** | 2.12 | Chưa có static SME data | Thấp: thêm vào ANSWER_SYSTEM_PROMPT. Priority medium. |

---

## 💡 Khuyến nghị ưu tiên (Quick Wins)

### ✅ Đã hoàn thành trong sprint 2026-03-01

1. **Intent-driven TopK override** — PRICING/INFO_QUERY→5, COMPARISON→10 (step 2b)
2. **Relative Compression Threshold** — `max(0.25, best_sim×0.65)` → adaptive lọc chunks
3. **Confidence Threshold 0.45** — Calibrated sau 7 real queries với bge-m3
4. **Metadata Critic fix** — `is_valid = specs_valid` (keywords diagnostic only) → 16/19 enriched
5. **Keyword extraction timeout=45s** — Prevents qwen3:0.6b hang incident
6. **normalize_query → economy-chat** — Tránh Ollama model swap OOM
7. **Phoenix OTLP via gRPC** — `additional_span_processors` wired, Phoenix nhận spans

### Làm khi có UX/production data

1. **Dynamic threshold per intent** — Sau khi có logs từ `model_traces` để calibrate.
2. **Pinned State** — Thêm bảo hành, địa chỉ vào `ANSWER_SYSTEM_PROMPT` từ ENV/config.
3. **Graduated fallback responses** — 3 levels theo confidence range.

### Làm theo sprint (effort cao hơn)

4. **SupportingFact schema** — Khi có customer-facing UI.
5. **Cross-Encoder Reranking** — Week 7, khi dataset > 1k products.

---

*Audit lần đầu: 2026-02-28. Cập nhật lần 2: 2026-03-01 sau RAG optimization sprint.*  
*Source: toàn bộ source code trong `services/`, `models/`, `scripts/`, `migrations/`, `tests/`, `core/`.*  
*Tham chiếu: `week2.md` (developer knowledge base), `week2-techniques-overview.md` (spec).*
