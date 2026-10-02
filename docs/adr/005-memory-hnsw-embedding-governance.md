# ADR-005: Memory HNSW Index & Embedding Governance

**Date**: 2026-03-11  
**Status**: ACCEPTED  
**Authors**: AI Sales Agent Team  

---

## Context

Week 5 introduces semantic memory for the AI Sales Agent. Customer past interactions must be retrievable quickly when they return to chat. The challenge:

1. **Retrieval Performance (FR-009)**: Must respond in <500ms with top-3 most relevant past summaries
2. **Embedding Governance (FR-010)**: Embeddings come from a configurable model; changing models invalidates old embeddings
3. **Scale**: Support 1000s of customers, 100k+ semantic memory entries, queries must not degrade linearly
4. **SME Operational Budget**: Cannot afford dedicated vector DB (Pinecone, Weaviate) or complex infrastructure

---

## Decision

### A. HNSW Index on pgvector (PostgreSQL Native)

**Use PostgreSQL's pgvector with HNSW (Hierarchical Navigable Small World) index** for semantic similarity search:

```python
# In models/schema.py for SemanticMemory ORM model:
embedding = Column(Vector(1024), nullable=False)

# Index definition:
Index(
    "idx_semantic_memory_embedding_hnsw",
    embedding,
    postgresql_using="hnsw",
    postgresql_with={"m": 16, "ef_construction": 64},
    postgresql_ops={"embedding": "vector_cosine_ops"}
)
```

**HNSW Parameters** (based on pgvector 0.8 guidance):
- **m=16**: Node connectivity degree (default 16). Higher = more accurate but slower index build. 16 is SME-optimal.
- **ef_construction=64**: Search parameter during index build (default 64). Higher = more thorough construction. 64 is standard.
- **Vector Operator**: `vector_cosine_ops` — cosine distance (not L2), aligns with LLM embeddings (unit vectors)

**Per-session Search Tuning**:
```sql
SET LOCAL hnsw.ef_search = 40;  -- Search parameter (lower = faster, less thorough)
```

### B. Model Version as Composite Key in Embedding Rows

**Problem**: If we change EMBED_MODEL from `ollama/bge-m3` to `openai/text-embedding-3`, all old embeddings are distorted.

**Solution**: Store composite key `model_version = f"{model_name}@{dimension}"` per embedding row:

```python
# In SemanticMemory model:
embedding_model: str  # e.g., "ollama/bge-m3"
model_version: str    # e.g., "ollama/bge-m3@1024" (composite key)
```

**At Retrieval Time**:
- Get current model version: `current_version = f"{settings.EMBED_MODEL}@{settings.EMBED_DIMENSION}"`
- Query: `WHERE customer_id = :cid AND status = 'ACTIVE' AND model_version = :current_version`
- Old embeddings (different model_version) automatically excluded — no search on distorted vectors

**At Model Change**:
- `flag_stale()` task: `UPDATE semantic_memory SET status = 'STALE' WHERE model_version != current_version`
- Optional: `reembed-semantic-memory` CLI command re-embeds STALE rows (deferred, not blocking)

### C. Embedding Dimension Consistency

One embedding model per environment:
- **Dev**: `ollama/bge-m3` (1024 dim)
- **Staging**: `cohere/embed-english-light-v3.0` (384 dim) — optimized for cost
- **Prod**: (TBD by ops, but never mix dimensions in same DB)

**Enforcement**: Pydantic validator on SemanticMemory to catch dimension mismatches at insert time.

---

## Consequences

### Positive
- ✅ No new infrastructure (PostgreSQL + pgvector is already required by Week 4)
- ✅ HNSW is production-proven (used by Supabase, Neon for vector search)
- ✅ Query latency ~50–100ms for 100k entries (fast enough for SME)
- ✅ Model version tracking prevents silent search on corrupted embeddings
- ✅ Easy to migrate to dedicated vector DB later (query interface stays same)

### Tradeoffs
- ⚠️  HNSW index rebuild (on data mutation) is O(log n), not instant; but acceptable for async summarization tasks
- ⚠️  Dimension changes require re-embedding (deferred operation via CLI, not blocking)
- ⚠️  Must choose embedding model per environment; cannot mix multiple models in same DB

### Mitigation
- **Dimension mismatch**: Caught at Pydantic validation + INSERT trigger guard (FR-010)
- **Query latency**: HNSW parameters tuned for 100k entry scale; stress test in Phase 7
- **Model drift**: `flag_stale()` + CLI `reembed-semantic-memory` defers re-embedding work

---

## Alternatives Considered

### Alternative 1: In-Memory Vector Search (e.g., Faiss, Annoy)
**Rejected**: No persistence across restarts; requires separate cache invalidation; SME can't afford dedicated vector DB + caching layer.

### Alternative 2: Dedicated Vector DB (Pinecone, Weaviate, Qdrant)
**Rejected**: Monthly cost for SME budget; violates Article VII (single-DB principle); adds operational complexity.

### Alternative 3: Naive SQL Cosine Distance (No Index)
**Rejected**: O(n) query time for 100k entries is too slow (<500ms budget violated); CPU thrashing on prod.

### Alternative 4: L2 Distance Operator
**Rejected**: Cosine distance better aligns with normalized embeddings from LLMs; pgvector default.

---

## Implementation Checklist

- [x] HNSW index parameters documented (m=16, ef_construction=64, cosine_ops)
- [x] Model version composite key strategy defined
- [x] Status tracking for stale embeddings (ACTIVE / STALE)
- [x] `flag_stale()` service method for model transitions
- [x] `reembed-semantic-memory` CLI command specified
- [x] Per-environment embedding model choice documented
- [x] Validation guard against dimension mismatches
- [x] Week 7 stress test for latency (<500ms at 100k scale)

---

## References

- **pgvector Documentation**: https://github.com/pgvector/pgvector
- **HNSW Papers**: "Efficient and robust approximate nearest neighbor search in high dimensional spaces"
- **Week 5 Spec**: See spec.md FR-007 through FR-010b for memory requirements
- **Article VII (Constitution)**: Single-database-only principle

