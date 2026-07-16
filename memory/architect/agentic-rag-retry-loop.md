# Architect memory — agentic-rag-retry-loop

## 2026-07-15 — agentic-rag-retry-loop: reusable design lessons

- **Collapsed status booleans hide branch reasons — disambiguate with an existing secondary field
  before adding new behavior.** `RetrievalResult.declined` meant THREE different things (spam,
  embed-down, low-confidence). The retry loop only wanted the third. Rather than add an enum/reason
  field (spec churn), we distinguished via a field already on the object (`query_vector` presence).
  General pattern: when a new feature needs to branch on a subset of an existing boolean's causes,
  look for an already-populated field that separates them before extending the schema.
- **Place a bounded loop as a shared service-layer helper wrapping the existing single-pass function —
  not inlined in the orchestration node.** Keeps the loop testable in isolation, reuses the function's
  returned signals, and lets every entry point (graph node, batch consumer, HTTP-facing wrapper) get
  the behavior. Watch for duplicated flows (here `answer_with_rag` duplicated retrieval) — the shared
  helper is also the moment to collapse the duplication.
- **DoS-sensitive loops: mandate a bounded `for range(cap)` + kill-switch default in an ADR, and make
  the cap=0 path byte-identical to the pre-change behavior.** Gives ops an instant rollback without a
  deploy and makes regression testing trivial (assert old==new when cap=0).
- **Reuse the existing structured-output LLM call pattern for any new light-tier call** (here
  `normalize_query` → `rewrite_query`): same alias, `temperature=0`, heuristic pre-check, graceful
  `except` fallback. Consistency + free offline/zero-cost compliance.
