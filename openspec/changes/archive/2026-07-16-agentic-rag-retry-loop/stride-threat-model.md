# STRIDE Threat Model — agentic-rag-retry-loop (ticket 2026)

Scope: the new `retrieve_with_retry` loop + `AIGateway.rewrite_query`. No auth/payment/PII/upload/admin
surface is touched; the loop introduces a **cost/DoS** and a **prompt-injection** surface only.
Concise pass (config `security.stride_analysis = auto`); full mitigations in `design.md §Security`.

| # | STRIDE | Threat | Sev | Mitigation (design element / AC) |
|---|--------|--------|-----|----------------------------------|
| T1 | **Denial of Service** | Unbounded/recursive loop multiplies LLM+embed calls per query → cost blowup / resource exhaustion | **HIGH** | Bounded `for` loop (ADR-003), hard cap `RAG_RETRY_MAX_ATTEMPTS` 0..2 default 1, kill switch `=0`, no-progress early stop, light tier only. Cost provably ≤ N×(rewrite+search). AC-2026-013/017/015/014 |
| T2 | **Tampering / Information Disclosure** | Crafted user text steers the rewrite to retrieve unrelated catalog data | MED | Rewrite output used ONLY as a parameterized retrieval query (SQLAlchemy `select()`, R-SEC-003), never executed; catalog-scoped; `keeps_subject` constraint discards drift. AC-2026-008, BR-2026-004, RISK-002 |
| T3 | **Information Disclosure (logs)** | Rewritten query / trace leaks tokens or PII | LOW | Traces store product-search text + attempt# + guard decision only; no tokens/secrets/PII (R-SEC-002). AC-2026-021 |
| T4 | **Elevation (cost tier)** | Grader/rewriter forced onto premium/paid tier | LOW | `rewrite_query` hardcodes `economy-chat`; no model param exposed (AC-2026-010, EC-015) |

No unaddressed Critical. T1 (HIGH) fully mitigated structurally — the design's central constraint.
Spoofing / Repudiation: N/A (no identity or non-repudiation surface introduced).
