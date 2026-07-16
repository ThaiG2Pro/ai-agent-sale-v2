## 2026 — agentic-rag-retry-loop (S3 done: 2026-07-15)
### Dependencies (from other changes)
- None
### Shared Decisions
- ADR-001: one shared helper `retrieve_with_retry()` wraps `search_and_retrieve`; ALL RAG entry points route through it (also removes the `answer_with_rag` retrieval duplication).
- ADR-002: sufficiency gate REUSES `RetrievalResult` Layer-1 signals (`declined` + `query_vector` presence + `best_similarity`) — no new numeric scorer.
- ADR-003: bounded for-loop capped by `RAG_RETRY_MAX_ATTEMPTS` (0..2, default 1; 0 = kill switch = current static behavior).
- ADR-004: COMPARISON intent early-returns (mutually exclusive with the existing split — no double-retrieval storm).
### Exports (other changes may depend on these)
- `retrieve_with_retry(db, query, intent) -> RetrievalResult` (`services/rag/pipeline.py`) — retrieval with bounded self-eval → rewrite → retry.
- `AIGateway.rewrite_query(original) -> RewrittenQuery` (`services/ai.py`) — light-tier, intent-preserving query rewrite (mirrors `normalize_query`).
- `RAG_RETRY_MAX_ATTEMPTS` config field (`core/config.py`).
### Constraints Set (apply to subsequent changes)
- Any new RAG entry point MUST go through `retrieve_with_retry`, not `search_and_retrieve` directly.
- Grader/rewriter stays light-tier only (Zero-Cost/Offline-First); never add a second numeric scorer.
- Cache write stays in the answer path — never inside the retrieval loop (intermediate rewrites must not be cached).
---
