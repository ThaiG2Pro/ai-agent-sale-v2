# Agentic RAG Retry Loop — Requirements & Functional Specification

- **Change**: `agentic-rag-retry-loop`
- **Ticket**: 2026 · **Type**: cr (change request) · **Capability**: `rag-pipeline` (modified behavior)
- **Author**: analyst (S1+S2) · **Date**: 2026-07-15
- **Figma**: N/A (backend/pipeline behavior — no UI surface)

## S1 — Requirement Pack

### Problem Statement
The RAG pipeline (`services/rag/pipeline.py::search_and_retrieve` / `answer_with_rag`) is a **static,
single-pass** flow: `classify → normalize → cache → embed → hybrid RRF → compress → confidence guard`.
When the first retrieval is poor (low `best_similarity`, zero surviving chunks after compression), the
pipeline **immediately declines** — there is no mechanism to re-phrase the question and try again.
Per `docs/agent-orchestration-2026-research.md` §4 (recommendation #2 in §Tổng kết), this is the
clearest gap vs 2026 "agentic RAG": the industry now treats a bounded **self-evaluate → rewrite →
retry** reasoning loop as standard, not optional. The single existing retry is a hand-written
special case (`retrieval_node.py` COMPARISON regex split on `và/vs/với` → two searches merged) — a
patch for one intent, not a general mechanism.

### Why now / Value
- Highest-ROI upgrade identified in the 2026 research (recommendation #2), and it **reuses**
  `confidence_node`'s existing similarity/rerank scoring rather than adding a new scorer.
- Recovers answerable queries that a single embedding pass phrased poorly (typos, pronouns, terse
  Vietnamese phrasing) instead of declining them — directly improves sales-agent answer rate.

### Scope
#### In Scope
- A **controlled retry loop** around retrieval: after `search_and_retrieve`, self-evaluate the result
  using the **existing** confidence signals; if insufficient and budget remains, **rewrite the query**
  (light-tier local model) preserving intent, re-run retrieval, and re-evaluate.
- A **hard, configurable cap** on retries (default 1, max 2) plus no-progress and failure termination.
- Preservation of every existing fast-path (L1/L2 cache, `is_valid` spam guard, embedding-unavailable,
  COMPARISON split) and observability (`model_trace` per attempt).

#### Out of Scope (Non-Goals) — see `## Non-Goals`

### Stakeholders
- **Primary**: end customers (better answers), SME merchant (higher answer rate).
- **Technical**: backend/agent team (owns `services/rag`, `core/agent/nodes`), QC.

### Constraints
- **Offline-First / Zero-Cost-First** (`context/project.md`): grader/rewriter MUST run on a light-tier
  local Ollama model — never a premium/paid tier by default.
- **Async everywhere**: loop must stay fully async (ruff `ASYNC`); no blocking I/O.
- **Cost-bounded**: no runaway LLM cost — the loop is the single biggest cost-blowup risk of the change.
- Reuse `confidence_node` scores (`best_similarity`, `chunks_after`, `confidence_score`) — do NOT
  introduce a second numeric scorer.
- No new DB table required (reuse `model_traces`, `semantic_cache`).

## Non-Goals
- **NOT** HITL risk-score, episodic memory, MCP tool integration, or cascade verification-check —
  those are separate future changes (research §5, §6, §7, §9). This change is ONLY the RAG retry loop.
- **NOT** a rewrite of the whole pipeline — this adds one bounded loop around existing retrieval.
- **NOT** a learned/router-based query planner or multi-agent decomposition.
- **NOT** changing the confidence thresholds themselves (`LAYER1_CONFIDENCE_THRESHOLD`,
  `AGENT_CONFIDENCE_THRESHOLD`) or the RRF/compression logic.
- **NOT** lowering the decline bar: on exhaustion the pipeline does not force a weak answer.

## Assumptions
Each tagged with exactly one of [CONFIRMED] / [ASSUMED] / [MISSING] / [UNCLEAR].

- [CONFIRMED] The retry decision reuses existing confidence signals (`best_similarity`,
  `chunks_after`, `confidence_score`) — source: watch items + research §4 + `confidence.py`.
- [CONFIRMED] The grader/rewriter runs on the light tier via the `economy-chat` alias
  (`LIGHT_CHAT_MODEL = ollama/qwen3:0.6b`) — source: `services/ai.py::normalize_query` precedent + Zero-Cost principle.
- [CONFIRMED] Cache hits (L1/L2), `is_valid` spam guard, and embedding-unavailable fast-paths bypass
  the loop — source: `pipeline.py` early returns must be preserved.
- [ASSUMED] Default `RAG_RETRY_MAX_ATTEMPTS = 1`, allowed range `0..2` (0 = kill switch = current
  static behavior). Recommended conservative default for cost; **surface for confirmation**.
- [ASSUMED] On budget exhaustion while still below threshold, the pipeline keeps the **current decline
  behavior** (does not answer from weak chunks). Alternative "answer from best-so-far" would weaken
  grounding; **surface for confirmation**.
- [ASSUMED] The light model performs the **rewrite**; the numeric sufficiency gate stays the reused
  confidence score (the model does not become a new numeric scorer). **Surface for confirmation.**
- [ASSUMED] The loop lives around `search_and_retrieve` (new pipeline function or in `retrieval_node`);
  exact placement is an S3 design decision, not a spec constraint.
- [ASSUMED] The retry loop and the COMPARISON split are mutually exclusive per turn (no double
  retrieval storm) — the general loop supersedes/coordinates with the COMPARISON special case.

## Edge Cases
(≥10 across categories — `edge-case-enumerator`)

### Input Boundary
- EC-001: Empty / whitespace-only query → `is_valid` heuristic guard declines **before** the loop; no rewrite.
- EC-002: >500-word / high-token-density Vietnamese query → FTS 500-word truncation applies on **each**
  rewritten query; no unbounded token growth.
- EC-003: Rewrite returns empty string / only stop-words → treated as no-progress, loop stops.

### State Transition
- EC-004: First pass already sufficient (`sim ≥ LAYER1` and `chunks_after > 0`) → loop never entered.
- EC-005: Rewrite makes retrieval sufficient on attempt 1 → loop exits success, answer generated.
- EC-006: Budget exhausted, still insufficient → decline (or best-so-far per resolved default); never loop past cap.

### Concurrency
- EC-007: Same session, two rapid turns → loop is per-turn; no cross-turn bleed via checkpointer state.

### Data Integrity
- EC-008: Rewrite drifts the subject (asks about a different product) → discarded; original intent preserved.
- EC-009: No-progress: rewritten retrieval returns identical top `chunk_id`s or no `best_similarity` improvement → stop.

### Integration Failure
- EC-010: Grader/rewriter LLM timeout/unavailable → graceful fallback to current behavior (decline / best-so-far).
- EC-011: Embedding service down mid-loop → abort loop, decline (matches current embed-unavailable path); no partial state.
- EC-012: L1/L2 cache hit → skip loop entirely (no grader, no rewrite).

### Business Rule
- EC-013: `RAG_RETRY_MAX_ATTEMPTS = 0` → exact current static behavior (kill switch / backward compat).
- EC-014: COMPARISON query that also would trigger retry → no double retrieval storm (mutual exclusion).
- EC-015: Premium tier mis-configured for the grader → rejected/forced to light tier (Zero-Cost guard).

## Early Risk Flags
(QA early review — STRIDE-lite folded in; `security.stride_analysis = auto`. Feature does not touch
auth/payment/PII/tokens/upload/admin, but the loop introduces a cost/DoS + prompt-injection surface,
so a focused threat pass is included.)

- **RISK-001 — Runaway LLM cost / DoS (HIGH)** — STRIDE: *Denial of Service*. An unbounded loop
  multiplies LLM+embed calls per query. **Mitigation**: hard cap `RAG_RETRY_MAX_ATTEMPTS` (0..2,
  default 1) — BR-2026-001; per-attempt bound AC-2026-017; no-progress early stop AC-2026-015.
- **RISK-002 — Prompt injection via the rewrite prompt (MEDIUM)** — STRIDE: *Tampering / Information
  Disclosure*. Untrusted user text flows into the rewriter's prompt; a crafted query could try to
  steer the rewrite to retrieve unrelated catalog data. **Mitigation**: rewrite output is used ONLY as
  a retrieval query (never executed), retrieval stays parameterized (R-SEC-003), rewrite constrained
  to preserve intent (BR-2026-004), still scoped to the merchant catalog.
- **RISK-003 — Answer-quality regression from best-so-far (MEDIUM)** — if exhaustion answers from weak
  chunks, grounding degrades. **Mitigation**: keep the decline bar (BR-2026-009, default).
- **RISK-004 — Latency growth (MEDIUM)** — each retry adds light-LLM + embed + hybrid search. **Mitigation**:
  default cap 1, light tier only, no-progress early stop; observe via `model_trace` (BR-2026-008).
- **RISK-005 — Silent behavior change breaking single-pass tests (LOW)** — existing tests assume
  single-pass. **Mitigation**: kill switch `RAG_RETRY_MAX_ATTEMPTS=0` (BR-2026-007); per-attempt traces.
- **RISK-006 — Cache pollution with intermediate rewrites (LOW)** — **Mitigation**: only the final
  accepted answer/query is cached (AC-2026-023).

No unaddressed 🔴 Critical risks.

## Capabilities
- `rag-pipeline` — MODIFIED behavior (self-evaluate → rewrite → retry loop). **Note**: `openspec/specs/`
  is currently empty (this repo adopted OpenSpec recently and has no living RAG capability spec), so the
  spec delta is authored under `## ADDED Requirements` to establish the `rag-pipeline` capability spec
  for the first time. The requirements are written to describe the NEW agentic-retry behavior that
  supersedes the current static single-pass code. `deltaMode=MODIFIED` was requested but cannot
  reference a non-existent base — flagged for SPEC LOCK.

---

## S2 — Functional Specification

See `specs/rag-pipeline/spec.md` for the full requirement scenarios. Summary below.

### User Stories
- **US-1 — Self-evaluate retrieval sufficiency** (reuse confidence scores).
- **US-2 — Rewrite query preserving intent and retry** (light tier).
- **US-3 — Bound the loop** (budget · no-progress · failure).
- **US-4 — Preserve fast-paths & observability** (cache · COMPARISON · decline · traces).

### Business Rules
- BR-2026-001: The retry loop is hard-capped by `RAG_RETRY_MAX_ATTEMPTS` (config, range 0..2, default 1); it MUST NOT exceed the cap.
- BR-2026-002: The retry decision reuses existing confidence signals (`best_similarity`, `chunks_after`, `confidence_score`); no new numeric scorer is introduced.
- BR-2026-003: The grader/rewriter MUST run on the light tier (`economy-chat` / `LIGHT_CHAT_MODEL`) only — never a premium/paid tier (Zero-Cost / Offline-First).
- BR-2026-004: A query rewrite MUST preserve the original question's intent and product entities; a rewrite that changes the subject is invalid and discarded.
- BR-2026-005: No-progress guard — an identical rewritten query, identical top `chunk_id`s, or no `best_similarity` improvement stops the loop early.
- BR-2026-006: L1/L2 cache hits, the `is_valid` spam guard, and the embedding-unavailable path bypass the loop entirely.
- BR-2026-007: `RAG_RETRY_MAX_ATTEMPTS = 0` disables the loop → behavior identical to the current static pipeline (kill switch / backward compatibility).
- BR-2026-008: Every retry attempt records a `model_trace` (attempt number, rewrite, guard decision) for cost/quality observability.
- BR-2026-009: On budget exhaustion still below threshold, the pipeline keeps the current decline behavior — it does not lower the confidence bar to force an answer. [ASSUMED default]
- BR-2026-010: FTS 500-word truncation applies to each rewritten query (bounded token growth; Vietnamese token density).

### Integration Points
- INT-2026-001: RAG retry loop → `AIGateway` light tier (`economy-chat`) for grade/rewrite.
- INT-2026-002: RAG retry loop → `search_and_retrieve` / `hybrid_search_rrf` (re-retrieval per attempt).
- INT-2026-003: RAG retry loop → confidence signals (`best_similarity`, `chunks_after`) reused by `confidence_node`.
- INT-2026-004: RAG retry loop → `semantic_cache` (L1/L2 bypass + final cache write).
- INT-2026-005: RAG retry loop → `ModelTrace` persistence (per-attempt traces).
- INT-2026-006: RAG retry loop ↔ `retrieval_node` COMPARISON split (mutual exclusion / ordering).

### Non-functional Requirements
- **Performance**: default cap 1 keeps added latency to ≤1 extra (grade+embed+search) round-trip on the
  worst path; no-progress early stop caps wasted work.
- **Security**: R-SEC-003 parameterized retrieval preserved; rewrite output never executed; light-tier only.
- **Observability**: per-attempt `model_trace`; loop entry/exit logged via logfire (no PII/tokens in logs).

### Figma Design
Figma: N/A

---

## _Structured Extract

### AC List
- AC-2026-001: [CONFIRMED] Sufficient first pass (sim ≥ L1, chunks > 0) → accept, loop not entered (single-pass preserved)
- AC-2026-002: [ASSUMED] Sufficiency decision reuses best_similarity/chunks_after/confidence — no new numeric scorer
- AC-2026-003: [CONFIRMED] Insufficient first pass + budget remaining → enter loop instead of immediate decline
- AC-2026-004: [ASSUMED] Grader/evaluator malformed output → treated as insufficient, safe graceful handling
- AC-2026-005: [CONFIRMED] Zero chunks on first pass → treated as insufficient, triggers retry if budget
- AC-2026-006: [ASSUMED] Confidence signals unavailable (empty scores) → best_similarity=0 → insufficient, no crash
- AC-2026-007: [CONFIRMED] Insufficient result → light-tier LLM rewrites query, re-runs retrieval
- AC-2026-008: [CONFIRMED] Rewrite preserves original intent/entities (vi + en) — subject unchanged
- AC-2026-009: [ASSUMED] Successful rewrite → sufficient chunks → answer generated from improved retrieval (exit success)
- AC-2026-010: [CONFIRMED] Grader/rewriter MUST use light tier (economy-chat) — never premium/paid
- AC-2026-011: [ASSUMED] Rewrite LLM failure/timeout → no retry; return best-so-far / decline as today
- AC-2026-012: [ASSUMED] Rewrite empty/identical query → no-progress → loop stops, no wasted re-retrieval
- AC-2026-013: [CONFIRMED] Max attempts configurable (RAG_RETRY_MAX_ATTEMPTS 0..2, default 1); never exceeded
- AC-2026-014: [ASSUMED] RAG_RETRY_MAX_ATTEMPTS=0 disables loop → exact current static behavior
- AC-2026-015: [CONFIRMED] No-progress detection (same top chunk_ids or no sim improvement) → stop early
- AC-2026-016: [CONFIRMED] Budget exhausted + insufficient → decline (or best-so-far per default); never past cap
- AC-2026-017: [ASSUMED] Per-attempt bound: total added LLM calls ≤ max_attempts × (1 grade/rewrite + 1 embed/search)
- AC-2026-018: [ASSUMED] Mid-loop LLM/embed failure → abort loop, return best-so-far, no partial/corrupt state
- AC-2026-019: [CONFIRMED] L1/L2 cache hit → skip loop entirely (no grader, no rewrite)
- AC-2026-020: [ASSUMED] COMPARISON split still works; loop + COMPARISON do not both fire redundantly
- AC-2026-021: [ASSUMED] Each retry attempt writes a model_trace (attempt#, rewrite, guard decision)
- AC-2026-022: [CONFIRMED] is_valid spam/gibberish guard still declines before any loop (no rewrite of spam)
- AC-2026-023: [ASSUMED] Final cache write stores only the accepted final answer/query, not intermediate rewrites
- AC-2026-024: [ASSUMED] >500-word / high token-density query → FTS truncation each rewritten query; no unbounded growth

### Business Rules
- BR-2026-001: Loop hard-capped by RAG_RETRY_MAX_ATTEMPTS (0..2, default 1)
- BR-2026-002: Retry decision reuses existing confidence signals; no new scorer
- BR-2026-003: Grader/rewriter light tier only (economy-chat) — never premium
- BR-2026-004: Rewrite preserves intent/entities; subject-drift discarded
- BR-2026-005: No-progress guard stops the loop early
- BR-2026-006: Cache hits / is_valid guard / embed-unavailable bypass loop
- BR-2026-007: RAG_RETRY_MAX_ATTEMPTS=0 → static behavior (kill switch)
- BR-2026-008: Per-attempt model_trace for observability
- BR-2026-009: Exhaustion keeps current decline behavior (no forced weak answer) [ASSUMED]
- BR-2026-010: FTS 500-word truncation per rewritten query

### Integration Points
- INT-2026-001: RAG loop → AIGateway light tier (economy-chat)
- INT-2026-002: RAG loop → search_and_retrieve / hybrid_search_rrf
- INT-2026-003: RAG loop → confidence signals reused by confidence_node
- INT-2026-004: RAG loop → semantic_cache (L1/L2 bypass + final write)
- INT-2026-005: RAG loop → ModelTrace persistence
- INT-2026-006: RAG loop ↔ retrieval_node COMPARISON split (mutual exclusion)

### Risk Flags
- RISK-001: Runaway LLM cost / DoS — HIGH (STRIDE: DoS)
- RISK-002: Prompt injection via rewrite prompt — MEDIUM (STRIDE: Tampering/Info Disclosure)
- RISK-003: Answer-quality regression from best-so-far — MEDIUM
- RISK-004: Latency growth per retry — MEDIUM
- RISK-005: Silent behavior change breaking single-pass tests — LOW
- RISK-006: Cache pollution with intermediate rewrites — LOW

### Metadata
ticket_id: 2026
domain: rag-pipeline
has_figma: false
has_cms_ui: false
actors: [customer, sme_merchant, backend_team]
ac_count: 24
ac_confirmed: 11
ac_assumed: 13
ac_missing: 0
ac_unclear: 0
