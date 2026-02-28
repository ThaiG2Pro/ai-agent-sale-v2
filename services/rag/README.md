# RAG Pipeline Module

## Overview
Refactored from monolithic `services/rag.py` (657 lines) into modular, maintainable components.

**Public API** (from `services.rag.__init__.py`):
- `answer_with_rag()` — Main RAG orchestration
- `search_products()` — Semantic product search
- `ingest_product_text()` — Product ingestion

## Module Structure

### `__init__.py` (12 lines)
Public API export point. Imports and re-exports the three main functions for clean external interfaces.

### `constants.py` (55 lines)
Algorithm configuration and constants:
- `RRF_K = 60` — Reciprocal Rank Fusion constant
- `CONFIDENCE_THRESHOLD = 0.7` — Minimum similarity to answer
- `COMPRESSION_SCORE_THRESHOLD = 0.5` — Low-signal filter
- `NEAR_DUP_THRESHOLD = 0.80` — Near-duplicate removal threshold
- `ACTION_VERBS` — English + Vietnamese keywords for query classification
- `ANSWER_SYSTEM_PROMPT` — RAG system instruction

### `query.py` (34 lines)
Query understanding:
- `classify_query()` — Deterministic classification (short/long/ambiguous)
- `compute_adaptive_topk()` — Cost-efficient TopK selection (5/15/20)

Why split: Query classification is independent and used early in the pipeline.

### `compression.py` (53 lines)
Context reduction (FR-012):
- `compress_context()` — Deduplication + low-confidence filter + near-dup removal
- `_overlap_ratio()` — String similarity helper

Why split: Can be tested and tweaked independently of retrieval.

### `retrieval.py` (185 lines)
Vector and keyword search:
- `hybrid_search_rrf()` — RRF fusion of vector + FTS results with timeout protection
- `search_products()` — Simple vector search with timeout protection

Why split: Core retrieval logic decoupled from pipeline orchestration.

### `ingest.py` (60 lines)
Product knowledge population:
- `ingest_product_text()` — Create product + embedding with keyword extraction

Why split: Ingestion is a separate concern from querying.

### `pipeline.py` (245 lines)
Main RAG orchestration (FR-007):
- `RAGResult` — Pydantic output model
- `answer_with_rag()` — Full pipeline (13 steps)

Flow:
1. Classify query → Adaptive TopK
2. Normalize query → Canonical form + keywords
3. L1 cache check (exact match, O(1))
4. Embed query
5. L2 cache check (semantic match, threshold=0.95)
6. Truncate >500-word FTS queries
7. Hybrid retrieval (RRF fusion)
8. Similarity scoring
9. Context compression
10. Confidence guard (best_sim ≥ 0.7)
11. Build context + citations
12. LLM generation
13. Cache write (best-effort)

## Benefits

| Aspect | Old | New |
|--------|-----|-----|
| File size | 657 lines | ~80-245 per file |
| Testability | Monolithic | Independent modules |
| Reusability | Mixed concerns | Clear exports |
| Readability | Large scroll | Focused modules |
| Maintenance | Hard to trace | Clear dependencies |

## Imports

All external imports remain unchanged:
```python
from services.rag import answer_with_rag, search_products, ingest_product_text
```

Internal imports (within RAG package):
```python
# In pipeline.py
from services.rag.query import classify_query, compute_adaptive_topk
from services.rag.retrieval import hybrid_search_rrf
from services.rag.compression import compress_context
from services.rag.constants import CONFIDENCE_THRESHOLD, ANSWER_SYSTEM_PROMPT
```

## Testing Strategy

Each module can now be tested independently:
- `test_query.py` — Classification and TopK logic
- `test_compression.py` — Deduplication and filtering
- `test_retrieval.py` — Vector/FTS search
- `test_ingest.py` — Product ingestion
- `test_pipeline.py` — End-to-end orchestration

## Future Improvements

- Add type hints throughout (e.g., `AsyncSession` instead of bare `db`)
- Extract cache logic into separate module
- Add observability middleware per function
- Parameterize timeouts (currently hardcoded 10s)
