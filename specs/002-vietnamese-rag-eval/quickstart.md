# Quickstart: Vietnamese RAG Pipeline

**Feature**: `002-vietnamese-rag-eval`  
**Branch**: `002-vietnamese-rag-eval`

---

## Prerequisites

- Docker + Docker Compose running (`docker compose up -d`)
- `uv` installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Ollama running with bge-m3 model:

  ```bash
  ollama pull bge-m3
  ollama pull qwen2.5:7b   # economy-chat in dev
  ```

- `.env` file present (copy from `.env.example` and fill in values)

---

## 1. Start Infrastructure

```bash
docker compose up -d postgres
```

Verify:
```bash
docker compose ps   # postgres should be "Up"
```

---

## 2. Run Migrations

```bash
uv run alembic upgrade head
```

Expected output: `Running upgrade ... -> ... (head)`

---

## 3. Ingest Sample Products

```bash
uv run python scripts/seed_bulk.py
```

This creates 10+ Vietnamese SME products in `agent_v1.products` and generates
their bge-m3 embeddings in `agent_v1.text_embeddings`.

Verify:
```bash
uv run python -c "
import asyncio
from services.database import AsyncSessionLocal
from sqlalchemy import text
async def check():
    async with AsyncSessionLocal() as db:
        n = await db.execute(text('SELECT COUNT(*) FROM agent_v1.products'))
        e = await db.execute(text('SELECT COUNT(*) FROM agent_v1.text_embeddings'))
        print(f'Products: {n.scalar()}, Embeddings: {e.scalar()}')
asyncio.run(check())
"
```

---

## 4. Run Unit Tests (Deterministic Functions)

```bash
uv run python -m pytest tests/unit/test_rag_helpers.py -v
```

Expected: **40 passed** in ~6 seconds.

These tests cover:
- `classify_query()` — word-count + action-verb heuristics (FR-015)
- `compute_adaptive_topk()` — TopK 5/15/20 mapping (FR-009)
- `compress_context()` — 3-step compression (FR-012)
- `_overlap_ratio()` — SequenceMatcher ratio helper

---

## 5. Run Integration Tests

```bash
uv run python -m pytest tests/integration/test_rag.py -v
```

Requires live PostgreSQL. Tests cover `ingest_product_text()` and
`search_products()` end-to-end.

---

## 6. Run Evaluation CLI

### Tier 1 only (CI mode — no human interaction required)

```bash
uv run python scripts/tier1_eval.py --skip-tier2
```

Output: prints per-query Tier 1 pass/fail, saves `reports/eval_results.json`.

### Tier 1 + Tier 2 (human grading)

```bash
uv run python scripts/tier1_eval.py
```

For each query, you will see:
```
────────────────────────────────────────────────────────────
[vn_001] Giá sản phẩm X là bao nhiêu?
────────────────────────────────────────────────────────────
  Category  : short (TopK=5)
  Similarity: 0.8532 | Declined: False
  Tier 1    : ✓ PASS (kw 2/2, citations 3)

  Answer:
  Sản phẩm X có giá 299,000 VND...
  Citations : SKU-001, SKU-002

  Compression: 5→3 chunks

  Likert scale:
    1 = Completely wrong / irrelevant
    ...
    5 = Perfectly accurate, complete citations
    s = Skip (not graded)

  Your grade (1-5 or s):
```

Enter a number 1–5 or `s` to skip. After all queries:
```
════════════════════════════════════════════════════════════
  EVALUATION COMPLETE
  Cases          : 20
  Tier 1 Pass    : 16/20 (80%)
  Avg Keyword    : 72.50%
  Avg Similarity : 0.7931
  Declined       : 2
  Avg Human Grade: 3.85/5  (n=18)

  Results → reports/eval_results.json
```

---

## 7. Test Individual Functions (REPL)

```python
from services.rag import classify_query, compute_adaptive_topk, compress_context

# Query classification
classify_query("giá")                         # → "short"
classify_query("So sánh A và B về hiệu suất") # → "long"
classify_query(" ".join(["word"] * 20))       # → "ambiguous"

# Adaptive TopK
compute_adaptive_topk("giá bao nhiêu?")       # → 5

# Compression
chunks = [
    {"description": "Widget A has...", "vector_score": 0.9, "rrf_score": 0.1},
    {"description": "Widget A has...", "vector_score": 0.9, "rrf_score": 0.09},  # exact dup
    {"description": "Cheap product",  "vector_score": 0.3, "rrf_score": 0.05},  # score < 0.5
]
compress_context(chunks)  # → [{"description": "Widget A has...", ...}]
```

---

## 8. Test Full RAG Pipeline

```python
import asyncio
from services.database import AsyncSessionLocal
from services.rag import answer_with_rag

async def test():
    async with AsyncSessionLocal() as db:
        result = await answer_with_rag(db, "Sản phẩm nào có bảo hành 2 năm?")
        print(f"Declined: {result.declined}")
        print(f"Category: {result.query_category} (TopK={result.top_k_used})")
        print(f"Similarity: {result.best_similarity:.4f}")
        print(f"Answer: {result.answer[:200]}")
        print(f"Citations: {result.citations}")

asyncio.run(test())
```

---

## Key Files

| File | Purpose |
|------|---------|
| `services/rag.py` | Full RAG pipeline — hybrid search, compression, confidence guard |
| `services/ai.py` | AI gateway — LiteLLM routing, query normalization |
| `scripts/tier1_eval.py` | Evaluation CLI — Tier 1 heuristics + Tier 2 Likert |
| `tests/unit/test_rag_helpers.py` | 40 TDD unit tests for deterministic functions |
| `tests/eval/gold_dataset.json` | 20-item Vietnamese gold evaluation dataset |
| `models/schema.py` | SQLAlchemy models — Product, TextEmbedding, SemanticCache |
| `specs/002-vietnamese-rag-eval/` | Full feature documentation: spec, plan, research, data-model |

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | required | PostgreSQL async URL (`postgresql+asyncpg://...`) |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `EMBEDDING_MODEL` | `bge-m3` | Embedding model alias |
| `ECONOMY_MODEL` | `ollama/qwen2.5:7b` | Economy chat model |
| `PREMIUM_MODEL` | `ollama/llama3:70b` | Premium chat model (local fallback) |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

---

## Troubleshooting

**`bge-m3` not found**:
```bash
ollama pull bge-m3
```

**`pgvector` extension missing**:
```bash
docker compose exec postgres psql -U postgres -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

**`FTS search failed, falling back to vector-only`** in logs:
This is expected on queries with no matching tokens — the pipeline falls back
gracefully to vector-only results.

**`40 unit tests fail`**:
Ensure you're running with `uv run` (Python 3.13+ required):
```bash
uv run python --version  # must be 3.13+
```
