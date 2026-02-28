# 📚 WEEK 2 — TECHNIQUES OVERVIEW (Vietnamese RAG & Evaluation)

> **Mục đích:** Tổng hợp toàn bộ kỹ thuật từ báo cáo nghiên cứu (2.3 → 2.13) thành một tài liệu duy nhất, giúp ra quyết định triển khai từng task trong tuần 2.

---

## 📌 MỤC LỤC

1. [Kiến trúc tổng thể RAG 2026](#1-kiến-trúc-tổng-thể-rag-2026)
2. [Stack kỹ thuật cốt lõi (lặp lại xuyên suốt)](#2-stack-kỹ-thuật-cốt-lõi-lặp-lại-xuyên-suốt)
3. [Task 2.3 — Metadata Enrichment](#3-task-23--metadata-enrichment)
4. [Task 2.4 — Query Normalization](#4-task-24--query-normalization)
5. [Task 2.5 — Hybrid Search (Vector + FTS)](#5-task-25--hybrid-search-vector--fts)
6. [Task 2.6 — Gold Dataset (Vietnamese)](#6-task-26--gold-dataset-vietnamese)
7. [Task 2.7 — RAG Flow v1](#7-task-27--rag-flow-v1)
8. [Task 2.8 — Evaluation CLI Runner](#8-task-28--evaluation-cli-runner)
9. [Task 2.9 — Adaptive TopK](#9-task-29--adaptive-topk)
10. [Task 2.10 — Similarity Gap Scoring](#10-task-210--similarity-gap-scoring)
11. [Task 2.11 — Citation Metadata Mapping](#11-task-211--citation-metadata-mapping)
12. [Task 2.12 — Context Compression](#12-task-212--context-compression)
13. [Task 2.13 — Confidence Threshold Guard](#13-task-213--confidence-threshold-guard)
14. [Ma trận kỹ thuật trùng lặp](#14-ma-trận-kỹ-thuật-trùng-lặp)

---

## 1. Kiến trúc tổng thể RAG 2026

Năm 2026, RAG tiến hóa từ vector search thuần túy sang **mô hình lai đa chiều**. Luồng xử lý chuẩn:

```
Input (Raw Query - Teencode/Tiếng Việt)
  → Query Normalization (Task 2.4)
  → Semantic Cache Check (L1/L2)
  → Hybrid Search: Vector + FTS/BM25 (Task 2.5)
  → Adaptive TopK (Task 2.9)
  → Confidence Check (Task 2.13)
  → Conditional Rerank (Cross-Encoder)
  → Context Compression / Dedup (Task 2.12)
  → LLM Generation với Structured Output (Pydantic)
  → Citation Mapping (Task 2.11)
  → Store Trace → model_trace (Task 2.10)
```

**Mô hình Rewrite-Retrieve-Read** (Task 2.7):
- **Rewrite:** SLM (Qwen 1.5B–3B) chuẩn hóa câu hỏi thô
- **Retrieve:** PostgreSQL + pgvector hybrid search
- **Read:** LLM tổng hợp với grounding ngữ cảnh

---

## 2. Stack kỹ thuật cốt lõi (lặp lại xuyên suốt)

> Những kỹ thuật này xuất hiện trong **hầu hết** các task — cần nắm vững trước khi triển khai.

### 2.1 LiteLLM + Pydantic V2 — Structured Outputs

Tất cả báo cáo đều thống nhất: **không dùng regex parsing**, chỉ dùng `response_format` + Pydantic.

```python
from pydantic import BaseModel, Field
from litellm import acompletion

class OutputSchema(BaseModel):
    field: str = Field(...)

response = await acompletion(
    model="ollama/qwen3:14b",
    messages=[...],
    response_format=OutputSchema,
    temperature=0
)
validated = OutputSchema.model_validate_json(response.choices[0].message.content)
```

**Lợi ích:** Constrained decoding, loại bỏ token không hợp lệ, tự động fallback/retry.

### 2.2 PostgreSQL 17 + pgvector + JSONB

| Tính năng | Mục đích |
|---|---|
| **JSONB + GIN index** | Tìm kiếm metadata cực nhanh (toán tử `@>`) |
| **HNSW index** | Vector search nhanh hơn IVFFlat 17 lần |
| **Generated Column** (tsvector) | Tự động đồng bộ FTS index |
| **JSON_TABLE** | Join JSON lồng với bảng quan hệ, không cần ETL |
| **VACUUM cải tiến** | Quản lý bộ nhớ tốt hơn 20× so với bản cũ |

```sql
CREATE EXTENSION IF NOT EXISTS unaccent;
CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE products
ADD COLUMN content_tsvector tsvector
GENERATED ALWAYS AS (
    setweight(to_tsvector('vietnamese', unaccent(coalesce(name, ''))), 'A') ||
    setweight(to_tsvector('vietnamese', unaccent(coalesce(description, ''))), 'B')
) STORED;

CREATE INDEX idx_products_fts ON products USING GIN(content_tsvector);
CREATE INDEX idx_products_embedding ON products USING hnsw (embedding vector_cosine_ops);
```

### 2.3 Async Python (asyncio / AnyIO)

- **Non-blocking I/O:** Dùng `await` cho mọi DB query và LLM call.
- **Offload CPU tasks:** Dùng `anyio.to_thread.run_sync` cho Pydantic nặng hoặc local reranker.
- **Backpressure:** `anyio.CapacityLimiter` giới hạn concurrent requests.

### 2.4 Small Language Models (SLM) cho Dev

| Model | Dùng cho | Inference |
|---|---|---|
| Qwen2.5-0.5B/1.5B | Rewrite, chuẩn hóa | < 1s |
| Qwen2.5-3B | Query normalization | < 1s |
| Qwen3:14B | Metadata extraction, generation | 1–3s |
| Phi-3.5 Mini / Llama 3.2 3B | Đọc hiểu, NER | < 1s |

---

## 3. Task 2.3 — Metadata Enrichment

**Mục tiêu:** Trích xuất ≥5 keywords từ văn bản sản phẩm để làm giàu metadata.

### Vấn đề cốt lõi
Metadata 2026 là **tín hiệu ưu tiên hàng đầu**, không chỉ là bộ lọc hậu truy xuất. Phân biệt tài liệu có ngôn ngữ giống nhau nhưng khác thực thể/thời gian.

### Kỹ thuật chính

**A. Pydantic Schema cho Metadata:**
```python
from pydantic import BaseModel, Field
from typing import List

class ProductMetadata(BaseModel):
    product_id: str = Field(..., description="SKU hoặc Model number")
    technical_specs: dict = Field(..., description="Điện áp, công suất, kích thước...")
    keywords: List[str] = Field(..., min_length=5, description="≥5 từ khóa cho hybrid search")
    seo_summary: str = Field(..., description="Tóm tắt < 50 từ")
```

**B. Async extraction với LiteLLM:**
```python
async def enrich_metadata(text: str) -> dict:
    response = await acompletion(
        model="ollama/qwen3:14b",
        messages=[{"role": "user", "content": text}],
        response_format=ProductMetadata,
        temperature=0
    )
    validated = ProductMetadata.model_validate_json(response.choices[0].message.content)
    return validated.model_dump()
```

**C. JSONB Storage Structure:**
```json
{
  "product_info": {
    "sku": "X-500-ULTRA",
    "category": "Linh kiện điện tử",
    "specs": {"voltage": "220V", "frequency": "50Hz"}
  },
  "seo_metadata": {
    "keywords": ["biến tần X-500", "tiết kiệm điện"],
    "intent": "commercial"
  }
}
```

**D. Kiểm soát ảo giác (Generator-Critic):**
- LLM 1 trích xuất → LLM 2 (suy luận cao hơn) kiểm tra lại với văn bản gốc.
- Sanitization: `model_validate_json()` lặp lại sau mỗi LLM call.

**E. Chunking nâng cao:**
- **Contextual Chunking:** Tạo "retrieval nuggets" (tên SP, SKU, dòng máy) đính kèm mỗi chunk trước khi embed.
- **Semantic Chunking:** Dùng vector similarity để chia đoạn → cải thiện Recall thêm 9%.
- **Late Chunking:** Xử lý toàn bộ tài liệu qua long-context model trước khi chia → giữ cross-reference.

**F. Prompt Engineering cho model nhỏ:**
- Dùng XML tags (`<Specs>`) và dải phân cách (`###`) để tránh context drift với Qwen3-4B.

**Nguyên tắc:** JSONB First → Structured Outputs → AnyIO worker pools → Generator-Critic QA.

---

## 4. Task 2.4 — Query Normalization

**Mục tiêu:** Chuẩn hóa truy vấn tiếng Việt (teencode, lỗi gõ) thành ngôn ngữ tìm kiếm chuẩn.

### Vấn đề: Đặc thù tiếng Việt TMĐT

| Biến thể | Chuẩn hóa | Phân loại |
|---|---|---|
| `ib` | inbox / nhắn tin | Từ mượn |
| `bn` / `nhiu vay` | bao nhiêu | Teencode |
| `đc` / `ok` | được | Viết tắt |
| `k` / `ko` / `khum` | không | Vùng miền |
| `giá nhiu` | giá bao nhiêu | Rút gọn |
| `đt` / `dt` | điện thoại | Viết tắt |

**Semantic drift:** Từ thừa ("shop ơi", "cho mình hỏi") đẩy tài liệu kỹ thuật ra khỏi top vector search.

### Kỹ thuật chính

**A. Pydantic Model `NormalizedQuery`:**
```python
class NormalizedQuery(BaseModel):
    original_query: str
    refined_query: str = Field(description="Đã sửa lỗi, chuẩn hóa từ viết tắt")
    detected_language: str = Field(description="ISO: 'vi', 'en'")
    search_keywords: List[str] = Field(description="Từ khóa và thực thể chính")
    is_valid: bool = Field(description="False nếu spam/gibberish")
```

**B. Async normalization call:**
```python
async def run_normalization(user_input: str) -> NormalizedQuery | None:
    response = await litellm.acompletion(
        model="ollama/qwen2.5-3b-instruct",
        api_base="http://localhost:11434",
        messages=[
            {"role": "system", "content": "Vietnamese E-commerce query normalization expert."},
            {"role": "user", "content": user_input}
        ],
        response_format=NormalizedQuery,
        temperature=0.1
    )
    return NormalizedQuery.model_validate_json(response.choices[0].message.content)
```

**C. LangGraph integration:**
```python
class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    normalized_data: NormalizedQuery
    context_docs: List
```
Flow: Input → **Normalization Node** → **Routing Node** (kiểm tra `is_valid`) → Retrieval → Generation.

**D. Đánh đổi Latency:**
- LLM call tốn 200ms–1000ms → dùng SLM (Qwen 2.5-3B).
- **Selective Normalization:** Chỉ gọi LLM khi phát hiện teencode phức tạp (heuristic trước).
- Prompt Caching + Parallel Processing để tối ưu.

**E. Bảo mật:**
- Delimiters (`<<<USER_INPUT>>>`) chống Prompt Injection.
- Gán `is_valid=False` qua few-shot prompting cho spam/gibberish.

**F. Multi-turn Query:**
- Truyền toàn bộ `messages` history → LLM xử lý ellipsis (tỉnh lược) dựa trên ngữ cảnh.

---

## 5. Task 2.5 — Hybrid Search (Vector + FTS)

**Mục tiêu:** Recall > vector-only bằng cách kết hợp semantic + lexical search.

### Lý do cần Hybrid

- **Vector search** mạnh về khái niệm nhưng bỏ lỡ mã sản phẩm, SKU, thông số kỹ thuật hiếm.
- **FTS** so khớp chính xác (exact match) dựa trên inverted index — rõ ràng với mã định danh.
- Embedding models thường tokenize mã đặc thù (ví dụ "IP15PM") thành sub-tokens vô nghĩa.

### Thuật toán xếp hạng

**A. Reciprocal Rank Fusion (RRF) — Tiêu chuẩn 2026:**

$$RRF\_score(d) = \sum_{r \in R} \frac{1}{k + rank_r(d)}, \quad k \approx 60$$

Ưu điểm: Dùng **rank** thay vì điểm số thô → kết hợp được hai hệ thống có thang điểm khác nhau.

**B. Weighted Sum (alternative):** 70% FTS + 30% Vector — cần ground truth để gán trọng số, yêu cầu chuẩn hóa BM25 về [0,1].

### Triển khai với SQLAlchemy 2.0 Async + CTE

```python
async def hybrid_search(
    session: AsyncSession,
    query_text: str,
    query_vector: list[float],
    limit: int = 5
) -> list:
    RRF_K = 60
    hybrid_query = text("""
        WITH semantic_search AS (
            SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> :vector) as rank
            FROM products ORDER BY embedding <=> :vector LIMIT 20
        ),
        keyword_search AS (
            SELECT id, ROW_NUMBER() OVER (
                ORDER BY ts_rank_cd(content_tsvector,
                    plainto_tsquery('vietnamese', unaccent(:query))) DESC
            ) as rank
            FROM products
            WHERE content_tsvector @@ plainto_tsquery('vietnamese', unaccent(:query))
            LIMIT 20
        )
        SELECT p.*,
            COALESCE(1.0 / (:rrf_k + s.rank), 0.0) +
            COALESCE(1.0 / (:rrf_k + k.rank), 0.0) AS hybrid_score
        FROM products p
        LEFT JOIN semantic_search s ON p.id = s.id
        LEFT JOIN keyword_search k ON p.id = k.id
        WHERE s.id IS NOT NULL OR k.id IS NOT NULL
        ORDER BY hybrid_score DESC
        LIMIT :limit
    """).bindparams(
        bindparam("vector", value=query_vector),
        bindparam("query", value=query_text),
        bindparam("rrf_k", value=RRF_K),
        bindparam("limit", value=limit)
    )
    result = await session.execute(hybrid_query)
    return result.mappings().all()
```

### Bảo mật & Tối ưu

- **Chống SQL Injection:** Luôn dùng `bindparams`, không dùng f-string với `to_tsquery`.
- **Sanitization:** Loại bỏ ký tự điều khiển tsquery (`&`, `|`, `!`) khỏi input.
- **Over-fetching:** Lấy 20–30 candidates mỗi nguồn trước RRF → tăng chính xác top-N.
- **Vietnamese Unaccent:** Extension `unaccent` + `TEXT SEARCH CONFIGURATION` cho tiếng Việt.

---

## 6. Task 2.6 — Gold Dataset (Vietnamese)

**Mục tiêu:** ≥10 truy vấn thực tế dạng JSON với triplet (Query, Context, Expected Answer).

### Cấu trúc Triplet chuẩn

```json
[
  {
    "id": "SME-SALES-001",
    "query": "gia may loc nuoc RO-X1 bn vay shop",
    "context": [
      "Máy lọc nước RO-X1 có giá niêm yết 5.500.000 VNĐ.",
      "Khuyến mãi tháng 2: tặng bộ lõi lọc trị giá 500k."
    ],
    "expected_answer": "Máy lọc nước RO-X1 có giá 5.500.000 VNĐ...",
    "metadata": {
      "category": "price_and_promotion",
      "difficulty": "medium",
      "input_type": "telex_with_typos"
    }
  }
]
```

### Pydantic Validation

```python
class GoldSample(BaseModel):
    id: str
    query: str = Field(..., min_length=10)
    context: List[str]
    expected_answer: str = Field(..., min_length=20)
    metadata: Optional[dict] = None

    @validator('context')
    def context_must_not_be_empty(cls, v):
        if not v:
            raise ValueError('Context không được để trống')
        return v
```

### Chiến lược thiết kế

**Phân bổ loại câu hỏi:**
| Loại | Tỷ lệ |
|---|---|
| Giá / Khuyến mãi | 40% |
| Thông số / SKU | 30% |
| So sánh sản phẩm | 20% |
| Bảo hành / Chính sách | 10% |

**Đặc thù ngôn ngữ cần bao quát:** Telex ("gia bnhieu"), thiếu chủ ngữ, từ lóng ngành ("chốt đơn").

### Hai trục đánh giá

1. **Retrieval Quality:** `Context Precision` + `Context Recall`
2. **Generation Quality:** `Faithfulness` (> 0.9) + `Answer Relevancy` (> 0.85)

$$Accuracy = \frac{\text{Số câu trả lời đúng}}{\text{Tổng số câu trong Gold Dataset}}$$

### Lưu ý quan trọng

- **Human-curated trước Synthetic:** Giai đoạn đầu ưu tiên dữ liệu con người biên soạn để xử lý edge cases.
- **Hiện tượng "Lost in the Middle":** LLM bỏ sót thông tin giữa đoạn dài → giải pháp: Reranking + Context Compression + đặt chỉ dẫn quan trọng ở đầu/cuối prompt.
- **PII Redaction:** Dùng `<NAME>`, `<PHONE>` thay thế thông tin cá nhân. Công cụ: Microsoft Presidio, SpaCy.
- **LLM-as-a-judge:** Dùng model tốt (Groq/GPT) chấm điểm tự động.
- **Human-in-the-loop calibration:** Người dùng điều chỉnh rubric cho AI judge.

---

## 7. Task 2.7 — RAG Flow v1

**Mục tiêu:** Pipeline hoàn chỉnh Rewrite → Search → Answer.

### Luồng xử lý đầy đủ

```
Input → Normalization → Hybrid Search → Confidence Check
     → Compression → Generation → Pydantic Structuring → Output
```

### Công nghệ cốt lõi

| Thành phần | Công nghệ | Vai trò |
|---|---|---|
| Điều phối | Python Async (asyncio) | Non-blocking, tối ưu latency |
| Gateway | LiteLLM | Đa mô hình, theo dõi cost/token |
| Database | PostgreSQL + pgvector | Lưu quan hệ + vector đồng nhất |
| Prompt | Jinja2 Templates | Logic động trong chỉ dẫn |
| Data Control | Pydantic Models | Cấu trúc đầu ra đồng nhất |

### Xử lý Context Management (tránh Context Rot)

- **Threshold Filtering:** Chỉ giữ chunk có similarity **> 0.75**
- **Semantic Deduplication:** Hashing/clustering xóa trùng lặp
- **Extractive Summarization:** Qwen2.5-1.5B trích ý chính trước khi đưa vào LLM

### Xử lý tiếng Việt đặc thù

- **QueryNormalizer:** Xử lý NSW (viết tắt, tiếng lóng) + lỗi Telex/VNI
- **NER:** Trích xuất SKU, tên sản phẩm để metadata filtering
- **Cross-Encoder Reranking:** Loại bỏ nhiễu ngữ nghĩa sau RRF

### Prompt design & Bảo mật

- **Grounding:** Ép AI chỉ trả lời dựa trên context cung cấp
- **XML tags / Markdown fences:** Chặn Prompt Injection
- **Citation bắt buộc:** Trích xuất `sources` qua Pydantic để tăng tỷ lệ chốt đơn

### Confidence Threshold Guard (tích hợp vào flow)

- Similarity < 0.7 → Fallback (kết nối người thật)

### Metrics target (RAGAs)

| Chỉ số | Target |
|---|---|
| Faithfulness | > 0.9 |
| Answer Relevancy | > 0.85 |
| Context Precision | > 0.8 |
| Context Recall | > 0.9 |

---

## 8. Task 2.8 — Evaluation CLI Runner

> **Lưu ý:** Báo cáo kỹ thuật cho task này chưa được cung cấp trong tuần 2.

**Định nghĩa hoàn thành theo project-log:**
- CLI hiển thị test cases cho human grading (HITL)
- Dùng custom Python script + LLM grading — không dùng framework nặng như Ragas
- LiteLLM đóng vai trò gateway cho LLM-as-a-judge (có thể dùng Groq/local Qwen)

---

## 9. Task 2.9 — Adaptive TopK

**Mục tiêu:** TopK động dựa trên độ dài và ý định truy vấn.

### Nguyên lý

- **TopK thấp:** Truy vấn thực tế (factual) → tránh noise
- **TopK cao:** Truy vấn so sánh/phân tích → phủ rộng nguồn
- **"Attention rot":** Quá nhiều context → phá hủy suy luận của GPT-4o (U-shaped behavior: chỉ nhớ đầu và cuối)

### Bảng gợi ý TopK

| Loại truy vấn | Đặc điểm | TopK |
|---|---|---|
| Đơn giản | Ngắn, thực thể cụ thể | 3–5 |
| So sánh | "khác gì", "so với", "tốt hơn" | 15–20 |
| Mơ hồ | Thiếu ngữ cảnh | 20 |
| Kỹ thuật | Quy trình, logic | 5–10 |

### Triển khai Python Heuristics (< 1ms, không tốn model call)

```python
import tiktoken

def get_dynamic_topk(query: str, intent: str = "general") -> int:
    words = query.strip().split()
    word_count = len(words)

    if word_count == 0:
        return 5

    if intent == "ambiguous":
        return 20

    comparative_keywords = ["khác gì", "so với", "khác nhau", "tốt hơn", "vs"]
    if any(kw in query.lower() for kw in comparative_keywords) or word_count > 15:
        return 15

    if word_count < 5:
        return 5

    return 10  # Default an toàn
```

### Chiến lược nâng cao

- **Pruning sau retrieve:** TopK = 30 → Reranker chọn 5–10 tốt nhất
- **Reranking + TopK:** Lấy TopK lớn rồi rerank thay vì TopK nhỏ trực tiếp
- **Context Engineering:** XML tags / JSON để LLM trích dẫn nguồn chính xác

### Hard Limit & Bảo mật

- **Hard Limit = 30** (tuyệt đối): Ngăn DoS via TopK exhaustion (cố ý nhập query dài)
- **Giám sát:** Langfuse / Braintrust theo dõi đột biến token

### Tầm nhìn RAG Agentic

- **Multi-step retrieval:** Nhiều lần retrieve nhỏ chính xác thay vì một lần lớn
- **HyDE (Hypothetical Document Embeddings):** Tạo câu trả lời giả định để search với K nhỏ hơn
- **Knowledge Graphs:** Kết hợp vector search + đồ thị tri thức cho câu hỏi logic

---

## 10. Task 2.10 — Similarity Gap Scoring

**Mục tiêu:** Tính khoảng cách giữa top-1 và top-2 để đánh giá độ tin cậy truy xuất.

### Công thức Gap

$$Gap_{abs} = Score_{top1} - Score_{top2}$$

$$Gap_{rel} = \frac{Score_{top1} - Score_{top2}}{Score_{top1}}$$

### Bảng phân tích kịch bản

| Top 1 | Top 2 | Gap Tuyệt đối | Gap Tương đối | Độ tin cậy |
|---|---|---|---|---|
| 0.95 | 0.30 | 0.65 | 0.68 | Rất Cao |
| 0.88 | 0.86 | 0.02 | 0.02 | Trung bình |
| 0.50 | 0.10 | 0.40 | 0.80 | Khá |
| 0.40 | 0.38 | 0.02 | 0.05 | Rất Thấp |

### Dynamic Routing dựa trên Gap

| Phân phối Score | Tín hiệu | Hành động Agent |
|---|---|---|
| Gap lớn + Score cao | Tin cậy | Dùng Small LLM trả lời ngay |
| Gap nhỏ + Score cao | Mơ hồ | Kích hoạt Reranking |
| Gap nhỏ + Score thấp | Không tìm thấy | Query Expansion hoặc tìm web |

### Implementation

```python
async def calculate_retrieval_confidence(results: list) -> dict:
    if not results:
        return {"max_score": 0.0, "min_score": 0.0, "top_gap": 0.0, "confidence": "none"}

    scores = [r.similarity for r in results]
    max_score = scores[0]
    min_score = scores[-1]

    if len(scores) < 2:
        top_gap = max_score
        confidence_label = "unique_match"
    else:
        top_gap = max_score - scores[1]
        confidence_label = "multi_match"

    return {
        "max_score": max_score,
        "min_score": min_score,
        "top_gap": top_gap,
        "confidence_type": confidence_label
    }
```

### Bảng model_trace — Schema đề xuất

| Trường | Kiểu | Mô tả |
|---|---|---|
| `trace_id` | UUID | Khóa chính |
| `query_vector` | vector(N) | Vector câu hỏi |
| `max_score` | FLOAT | Điểm tương đồng Top 1 |
| `top_gap` | FLOAT | Gap Top1 – Top2 |
| `confidence_level` | FLOAT | Điểm tin cậy tổng hợp |

**Definition of Done:**
1. Mọi truy xuất phải sinh ra `similarity_gap`
2. Chỉ số xuất hiện trong `model_trace`
3. Agent thay đổi hành vi dựa trên Gap
4. Xử lý edge case: 0 hoặc 1 tài liệu trả về

**Ứng dụng thêm:** Lưu vào `model_trace` để tối ưu embedding model, prompt engineering và tuân thủ EU AI Act.

---

## 11. Task 2.11 — Citation Metadata Mapping

**Mục tiêu:** Trả về ProductID/ChunkID kèm câu trả lời, ngăn ảo giác trích dẫn.

### Pydantic Schema trích dẫn

```python
from pydantic import BaseModel, Field, field_validator, ValidationInfo
from typing import List, Optional

class SupportingFact(BaseModel):
    fact: str = Field(..., description="Tuyên bố hoặc sự thật")
    substring_quote: List[str] = Field(..., description="Trích dẫn nguyên văn từ nguồn")
    source_id: str = Field(..., description="ID tài liệu nguồn trong PostgreSQL")
    relevance_score: float = Field(..., description="Độ liên quan 0–1")

class SalesResponse(BaseModel):
    answer: str = Field(..., description="Câu trả lời đầy đủ cho khách hàng")
    supporting_facts: List[SupportingFact] = Field(..., description="Danh sách trích dẫn kiểm chứng")
    product_id: Optional[str] = Field(None, description="ID sản phẩm nếu có hành động bán hàng")
```

**Validation Context:** `@field_validator` thực hiện substring check — nếu `substring_quote` không tồn tại trong văn bản gốc → báo lỗi, LLM tự điều chỉnh.

### LiteLLM Tag Routing & Fallback

- **Tag Routing:** Định tuyến theo thẻ (`sales-high-priority`)
- **Metadata passthrough:** Phân tích hiệu quả RAG theo session
- **Fallback chains + retries:** Tự động chuyển model khi lỗi, kèm `response_cost` tracking

### Chiến lược chống ảo giác 4 tầng (< 0.1% citation error)

1. **Source Verification:** Hash dấu vân tay kỹ thuật số của nguồn
2. **Traceable Annotation:** Đánh số `numbered source blocks` trước khi gửi LLM
3. **In-flight Validation:** Giám sát Semantic Entropy + Span-level Verification trong khi generate
4. **Post-generation Audit:** Script "Sanitizer" đối soát ID nguồn + kiểm tra URL/DOI

### PostgreSQL 17 JSONB — Hai loại GIN index

- `jsonb_path_ops`: Nhanh hơn cho toán tử containment (`@>`)
- `jsonb_ops`: Linh hoạt cho truy vấn key/value riêng lẻ

### Ứng dụng bán hàng

- **Tool-calling:** Kết nối metadata → logic thương mại (`check_inventory()`, `add_to_cart`)
- **Trust Building:** Hiển thị trích dẫn dưới dạng tooltip/deep links → tăng Add-to-Cart Conversion 6%–12%

### Bảo mật (EU AI Act 2026)

- **ABAC:** Kiểm soát truy cập tài liệu cấp hàng dựa trên JSONB metadata
- **Deep Attestation:** TPM ký số metadata, chống Index Poisoning
- **Audit Trail:** Lưu toàn bộ thought process vào JSONB log

---

## 12. Task 2.12 — Context Compression

**Mục tiêu:** Loại bỏ trùng lặp và nén context trước khi gửi LLM, giảm 20–80% token.

### Phương pháp Deduplication (Logic-First, không tốn LLM)

| Phương pháp | Cơ chế | Đặc điểm |
|---|---|---|
| **ID-based Dedup** | Khớp metadata ProductID | Cực nhanh, 0 chi phí |
| **Hash-based Dedup** | MD5/SHA băm nội dung | Loại bỏ bản sao tuyệt đối |
| **Semantic Dedup** | Embedding + Clustering | Phát hiện trùng ý nghĩa (tốn GPU) |
| **Fuzzy Matching** | Levenshtein distance | Xử lý lỗi chính tả |

```python
import asyncio
import hashlib

async def compress_by_id(chunks: list[dict]) -> list[dict]:
    seen_ids = set()
    unique_chunks = []
    for chunk in chunks:
        p_id = chunk.get("metadata", {}).get("ProductID")
        if not p_id:
            p_id = hashlib.md5(chunk["content"].encode()).hexdigest()
        if p_id not in seen_ids:
            seen_ids.add(p_id)
            unique_chunks.append(chunk)
    return unique_chunks
```

### Nén bằng Small Model (SMC)

- Dùng `qwen2.5-1.5b` hoặc `phi-3` qua LiteLLM làm proxy tóm tắt trước GPT-4o.
- **ACC-RAG & SeleCom:** Điều chỉnh tỷ lệ nén linh hoạt. SeleCom = query-conditioned information selector.
- **Điểm hòa vốn:** Context > 2,000 token → kết hợp Qwen 1.5B + GPT-4o giảm 80% chi phí token đầu vào.

### Công thức bảo tồn thông tin (erank)

$$erank(Z) = \exp\left( - \sum_{i=1}^{r} p_i \log p_i \right)$$

Duy trì hạng hiệu quả của embedding để bảo tồn thông tin gốc.

### Đặc thù SME Việt Nam

- Dữ liệu SME lặp lại chính sách bảo hành, địa chỉ, vận chuyển → đưa vào **Pinned State** trong `system prompt`.
- Benchmark tiếng Việt: VCS (Vietnam Context Benchmark), BERT-base với mean pooling.

### Luồng RAG tích hợp (Task 2.12)

```
Hybrid Search → compress_by_id (dedup) → SMC summarize → LLM Generation
```

### Bảo mật Multi-tenant (Two-Channel)

1. **Raw Channel:** Giữ giá trị gốc và ID tham chiếu
2. **Compressed Channel:** Bản nén để LLM lập luận
- `Isolating Context` theo Tenant ID
- `Sensitive Filter` dùng Regex/NER
- `Audit Logging` để kiểm toán lỗi AI

---

## 13. Task 2.13 — Confidence Threshold Guard

**Mục tiêu:** Nếu similarity < 0.7 → trả về "Không tìm thấy thông tin liên quan..." (chống hallucination).

### Chiến lược chọn ngưỡng

| Ngưỡng | Mức tin cậy | Phân khúc |
|---|---|---|
| > 0.90 | Tuyệt đối | Mã lỗi, liều lượng thuốc |
| 0.80–0.89 | Cao | Bảo hành, báo giá |
| 0.70–0.79 | Trung bình | Tư vấn sản phẩm, FAQ |
| < 0.70 | Thấp | Rủi ro hallucination |

### Cơ chế Similarity Gap kết hợp (Task 2.10 + 2.13)

- **Gap > 0.15:** Kết quả duy nhất, độ tin cậy cao
- **Gap < 0.01:** Dữ liệu trùng lặp hoặc mơ hồ → kích hoạt Clarification

### Implementation Async

```python
async def get_rag_context(query_embedding: List[float], intent: str):
    # Ngưỡng động theo intent từ ENV variable
    threshold = float(os.getenv(f"THRESHOLD_{intent.upper()}", 0.75))

    results = await db.fetch_vector_search(query_embedding, limit=5)

    if not results or results.similarity < threshold:
        await db.log_trace(query, results.similarity if results else 0, "LOW_CONFIDENCE")
        return None  # Kích hoạt Fallback ở tầng API

    return results
```

### Pydantic Response Schema

| Thuộc tính | Kiểu | Vai trò |
|---|---|---|
| `answer` | `str` | Câu trả lời hoặc Fallback message |
| `confidence_score` | `float` | Điểm tương đồng (0.0–1.0) |
| `is_fallback` | `bool` | True nếu bị chặn bởi Guard |
| `sources` | `List[str]` | Danh sách nguồn tài liệu |

### Fallback tinh tế (chuyển thất bại thành cơ hội)

| Confidence range | Hành động |
|---|---|
| 0.60–0.69 | Gợi ý Quick Replies |
| 0.40–0.59 | Gợi ý chủ đề liên quan |
| < 0.40 | Chuyển hướng Human-in-the-loop |

### Reranking 2 giai đoạn

1. **Candidate Selection:** pgvector + BM25 → 50–100 ứng viên
2. **Precision Reranking:** Cross-Encoder chấm điểm lại → Confidence Guard áp ngưỡng

**Cấu hình đa kênh:** `THRESHOLD_ZALO`, `THRESHOLD_FACEBOOK` — ngưỡng linh hoạt theo kênh giao tiếp.

### Ghi log vào model_trace

| Trường | Mục đích |
|---|---|
| `request_id` | Định danh phiên chat |
| `top_similarity` | Điểm tương đồng cao nhất |
| `similarity_gap` | Khoảng cách Top1 – Top2 |
| `guard_decision` | `ACCEPTED` / `REJECTED` / `FALLBACK` |

---

## 14. Ma trận kỹ thuật trùng lặp

> Bảng này giúp xác định kỹ thuật **triển khai một lần, dùng chung nhiều task**.

| Kỹ thuật | 2.3 | 2.4 | 2.5 | 2.6 | 2.7 | 2.9 | 2.10 | 2.11 | 2.12 | 2.13 |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **LiteLLM + Pydantic Structured Output** | ✓ | ✓ | | ✓ | ✓ | | | ✓ | ✓ | ✓ |
| **PostgreSQL 17 + pgvector** | ✓ | | ✓ | | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| **JSONB + GIN Index** | ✓ | | ✓ | | | | | ✓ | | |
| **Hybrid Search (Vector + FTS/BM25)** | ✓ | | ✓ | | ✓ | | | | ✓ | ✓ |
| **RRF (Reciprocal Rank Fusion)** | | | ✓ | | ✓ | | | | | |
| **Cross-Encoder Reranking** | | | | | ✓ | ✓ | | | | ✓ |
| **Async Python (asyncio/anyio)** | ✓ | ✓ | ✓ | | ✓ | | ✓ | | ✓ | ✓ |
| **Small Language Models (Qwen, Phi)** | ✓ | ✓ | | | ✓ | | | | ✓ | |
| **model_trace logging** | | | | | | | ✓ | | | ✓ |
| **Similarity Gap** | | | | | | | ✓ | | | ✓ |
| **Confidence Threshold** | | | | | ✓ | | ✓ | | | ✓ |
| **Tiếng Việt / Teencode handling** | | ✓ | ✓ | ✓ | ✓ | | | | ✓ | |
| **LangGraph State (TypedDict)** | | ✓ | | | ✓ | | | | | |
| **Context Dedup / Compression** | | | | | ✓ | ✓ | | | ✓ | |

---

## 💡 Khuyến nghị ưu tiên triển khai

### Ưu tiên 1 — Nền tảng (triển khai trước, dùng xuyên suốt)
1. **PostgreSQL schema** với HNSW + GIN + JSONB + Generated tsvector column
2. **LiteLLM wrapper** + Pydantic response schemas cho tất cả LLM calls
3. **Async session** (SQLAlchemy) + anyio.to_thread cho CPU tasks

### Ưu tiên 2 — Core RAG logic
4. **Hybrid Search + RRF** (Task 2.5) — nền tảng cho tất cả retrieval
5. **Query Normalization** (Task 2.4) — lớp đầu chuỗi, tối ưu recall ngay từ đầu
6. **Adaptive TopK** (Task 2.9) — gắn vào retrieval function

### Ưu tiên 3 — Chất lượng & Quan sát
7. **Similarity Gap Scoring + model_trace** (Task 2.10) — logging chuẩn cho mọi trace
8. **Confidence Threshold Guard** (Task 2.13) — gác cổng trước LLM call
9. **Context Compression** (Task 2.12) — giảm chi phí token

### Ưu tiên 4 — Enrichment & Evaluation
10. **Metadata Enrichment** (Task 2.3) — chạy offline khi ingest
11. **Citation Mapping** (Task 2.11) — cải thiện trust + conversion
12. **Gold Dataset** (Task 2.6) + **Eval CLI** (Task 2.8) — đánh giá cuối giai đoạn

---

*Tài liệu này được tổng hợp từ các báo cáo nghiên cứu: 2.3.md, 2.4.md, 2.5.md, 2.6.md, 2.7.md, 2.9.md, 2.10.md, 2.11.md, 2.12.md, 2.13.md*
