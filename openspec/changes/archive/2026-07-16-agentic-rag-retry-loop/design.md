## Sketch — Gap Analysis

**No critical gaps found.** The 24 ACs map cleanly onto existing machinery — the loop reuses
`search_and_retrieve`, its returned `RetrievalResult` signals, `AIGateway` (light tier), `ModelTrace`,
and `semantic_cache`. No new DB table, no new external dependency, no API contract change.

Minor gaps → handled as documented assumptions, not blockers:
- **G1 — `confidence_score` (Layer-2 fused) is not available at loop time.** The loop runs inside
  `retrieval_node`, *before* `confidence_node` computes the reranked fusion. So the sufficiency gate
  reuses the **Layer-1** signals only (`best_similarity`, `chunks_after`, already collapsed into
  `RetrievalResult.declined`). This still satisfies AC-2026-002 ("reuse existing signals, no new
  scorer") — see ADR-002. Documented, not a spec conflict.
- **G2 — Subject-drift detection (BR-2026-004 / AC-2026-008) cannot be done with a cheap offline NER.**
  Mitigated by a self-declared `keeps_subject` flag from the light rewriter + prompt constraint; residual
  robustness risk noted in Risk Assessment for QA. Not a blocker.
- **G3 — `answer_with_rag` duplicates retrieval** (does not call `search_and_retrieve`). Placement
  decision (ADR-001) resolves which flows get the loop.

No S2 return recommended. SPEC LOCK resolutions (cap default=1, exhaustion=decline, deltaMode=ADDED)
are built in.

## Context

`services/rag/pipeline.py` runs a **static single-pass** retrieval: `classify → normalize → L1/L2
cache → embed → hybrid RRF → compress → Layer-1 guard`. On a poor first retrieval (low
`best_similarity` or zero surviving chunks) it declines immediately — no re-phrase, no retry. This
change adds a **bounded self-evaluate → rewrite → re-retrieve loop** that recovers answerable queries,
reusing the confidence signals the pipeline already computes (research §4, recommendation #2).

Two retrieval flows exist today:
- `search_and_retrieve()` → `RetrievalResult` (retrieval only, no LLM gen) — used by the LangGraph
  production path (`retrieval_node` via `make_retrieval_tool`), the COMPARISON split, and `queue_consumer`.
- `answer_with_rag()` → `RAGResult` (retrieval **duplicated inline** + LLM gen + trace + cache write) —
  used by `POST /query`, `make_rag_tool`, and the CLI.

Constraints: Offline-First / Zero-Cost-First (light tier only), async everywhere (ruff `ASYNC`),
cost-bounded (the single biggest risk), reuse confidence scores (no second scorer), no new DB table.

## Goals / Non-Goals

**Goals:**
- A bounded, terminating retry loop around retrieval that reuses Layer-1 confidence signals.
- Structural cap `RAG_RETRY_MAX_ATTEMPTS` (0..2, default 1; 0 = exact current behavior).
- Preserve every fast-path (L1/L2 cache, `is_valid` guard, embed-unavailable, COMPARISON split) and
  per-attempt `model_trace` observability.

**Non-Goals:**
- No new scorer, no threshold changes, no RRF/compression changes, no HITL/memory/MCP work.
- No API contract change (see API Design — openapi N/A).
- Not answering from best-so-far on exhaustion (SPEC LOCK: keep decline).

## Architecture Overview

New shared helper **`retrieve_with_retry(db, query, intent) -> RetrievalResult`** in
`services/rag/pipeline.py`, layered *above* `search_and_retrieve` (services layer; no new import
edges — `core/agent` already imports `pipeline`). It:
1. calls `search_and_retrieve` (attempt 0),
2. evaluates sufficiency from the returned signals,
3. if retry-eligible and budget remains, calls `AIGateway.rewrite_query` (light tier) → re-calls
   `search_and_retrieve` with the rewrite, up to `RAG_RETRY_MAX_ATTEMPTS` times,
4. returns the accepted result, or the best-seen declined result on exhaustion/failure.

Wiring:
- `retrieval_node` calls `retrieve_with_retry` instead of `search_and_retrieve` (production path).
- `queue_consumer` (`INFO_QUERY` batch) calls `retrieve_with_retry`.
- `answer_with_rag` — see ADR-001 (routed through the same helper).

Dependencies reused (design.md §Reuse — Adopt): `search_and_retrieve`, `RetrievalResult`,
`AIGateway` (`services/ai.py`), `_write_model_trace` + `ModelTrace` (`models/schema.py`),
`semantic_cache`, `core/config.settings`. **New code = one function + one `AIGateway.rewrite_query`
method + one Pydantic `RewrittenQuery` model + one config field.**

## Decisions

### ADR-001 — Loop placement: shared helper wrapping `search_and_retrieve`

**Context.** The loop must re-run retrieval per AC-2026-007, reuse the confidence signals
`search_and_retrieve` already returns, and not create a double-retrieval storm with the COMPARISON
split (AC-2026-020). `answer_with_rag` duplicates retrieval inline.

| Option | Pros | Cons |
|---|---|---|
| A. New `retrieve_with_retry()` wrapping `search_and_retrieve`; wire into all retrieval entry points | Single source of truth; reuses returned signals directly; smallest new surface; testable in isolation | `answer_with_rag` must be pointed at it (small refactor of its retrieval block) |
| B. Inline the loop inside `retrieval_node` only | Local; graph path only | `/query` + CLI (`answer_with_rag`) get no retry → inconsistent product behavior; loop logic trapped in an orchestration node |
| C. Duplicate loop in both `search_and_retrieve` and `answer_with_rag` | Both flows covered | Duplicates the loop; contradicts "one bounded loop"; double maintenance |

**Decision: Option A.** One `retrieve_with_retry()` helper. Wire `retrieval_node` and `queue_consumer`
onto it now. For `answer_with_rag`, replace its inline retrieval block (embed→guard, steps 4-10) with a
delegation to `retrieve_with_retry` + keep its existing generation/trace/cache-write — this also
removes the long-flagged retrieval duplication. Consistent retry behavior on every user-facing RAG
path (agent, `/query`, CLI). **Why not B:** `/query` is a primary interface; single-pass there is a
coverage gap. **Why not C:** re-introduces the duplication we are eliminating.

**Consequences.** `answer_with_rag` refactor touches the `/query` hot path → mitigated by the kill
switch (`RAG_RETRY_MAX_ATTEMPTS=0` ⇒ byte-identical to today) and regression tests (RISK-005). If
DESIGN REVIEW wants a smaller blast radius, the `answer_with_rag` wiring can be deferred (graph path
still fully covered) — surfaced as a design choice to confirm.

### ADR-002 — Sufficiency gate reuses Layer-1 signals; no new scorer

**Context.** AC-2026-002 forbids a second numeric scorer. At loop time (inside `retrieval_node`) the
Layer-2 fused `confidence_score` is not yet computed (rerank happens downstream in `confidence_node`).
`search_and_retrieve` already collapses the Layer-1 guard (`best_similarity < CONFIDENCE_THRESHOLD OR
chunks_after == 0`) into `RetrievalResult.declined`, and returns `best_similarity` + `query_vector`.

| Option | Pros | Cons |
|---|---|---|
| A. Gate on the existing `RetrievalResult` fields (`declined` + `query_vector` presence + `best_similarity`) | Zero new scoring; reuses computed signals verbatim; distinguishes retry-eligible from spam/embed-down | Requires reading two fields to classify decline reason |
| B. Add a light-model grader emitting a numeric quality score | "Smarter" gate | Violates AC-2026-002; adds cost/latency; second scorer |

**Decision: Option A.** Classify the first-pass outcome from existing fields:

| `declined` | `query_vector` | Meaning | Loop action |
|---|---|---|---|
| False | — | sufficient **or** cache hit | accept, no loop (AC-2026-001, AC-2026-019) |
| True | empty `[]` | spam (`is_valid`) or embed-unavailable | bypass loop, return as-is (AC-2026-022, AC-2026-011/018 embed) |
| True | populated | Layer-1 insufficiency | **retry-eligible** (AC-2026-003, AC-2026-005, AC-2026-006) |

`best_similarity` defaulting to 0.0 on empty scores naturally reads as insufficient (AC-2026-006) with
no crash. **No new numeric scorer** — the light model only rewrites (ADR-005).

### ADR-003 — Structural cap (bounded for-loop, not recursion)

**Decision (single reasonable approach — RISK-001).** A `for attempt in range(max_attempts)` bounded
by `settings.RAG_RETRY_MAX_ATTEMPTS` (new config field, `Field(default=1, ge=0, le=2)`). No recursion,
no `while`. The cap provably bounds added work to `≤ N × (1 rewrite + 1 embed/search)` (AC-2026-017,
AC-2026-013). `N=0` skips the loop body entirely → exact static behavior (AC-2026-014, kill switch).
Rationale: a bounded for-loop is the only structurally-provable termination guarantee for a DoS-sensitive
loop; recursion/`while` reintroduce runaway risk.

### ADR-004 — COMPARISON mutual exclusion

**Context.** COMPARISON queries already have a specialized recovery (regex split → merge) in
`retrieval_node`. Running the generic loop *and* the split on the same turn = double retrieval storm
(AC-2026-020, INT-2026-006).

| Option | Pros | Cons |
|---|---|---|
| A. `retrieve_with_retry` does **not** loop when `intent == "COMPARISON"` (single pass); existing split still fires on decline | One recovery per turn, structurally; zero change to split code | COMPARISON keeps its narrower recovery (acceptable — pre-existing) |
| B. Replace the split with the generic loop | One mechanism | Loses the intent-specific split; larger blast radius; out of scope |

**Decision: Option A.** `retrieve_with_retry` early-returns a single `search_and_retrieve` pass for
COMPARISON intent; `retrieval_node`'s existing split fallback then runs on decline as today. Generic
loop and split are mutually exclusive by construction.

### ADR-005 — Intent-preserving rewrite on the light tier (structured output)

**Context.** BR-2026-003/004, AC-2026-007/008/010. Precedent: `AIGateway.normalize_query`
(`economy-chat`, `response_format=<PydanticModel>`, `temperature=0`, graceful fallback).

**Decision.** New `AIGateway.rewrite_query(original: str) -> RewrittenQuery` mirroring
`normalize_query`: `model="economy-chat"` (light tier, hardcoded — never premium, AC-2026-010),
structured output `RewrittenQuery { query: str, keeps_subject: bool }`, prompt constrained to preserve
intent + product entities and forbid subject change. Alternative rejected (handoff D4): a boolean
"sufficient?" gate that overrides the numeric decision — the numeric gate stays ADR-002; the model
**only rewrites**. On malformed/unparseable output or exception → return a fallback signaling failure
(AC-2026-004, AC-2026-011). `keeps_subject == false` → discard rewrite, treat as no-progress
(BR-2026-004). **Rewrite output is used ONLY as the next retrieval query — never executed** (RISK-002).

## Control Flow & Sequence Flows

**`retrieve_with_retry(db, query, intent)` (the heart of this change):**

```
max = settings.RAG_RETRY_MAX_ATTEMPTS
result = await search_and_retrieve(db, query, intent)          # attempt 0 (existing path)
if intent == "COMPARISON":            return result            # ADR-004 mutual exclusion
if not result.declined:               return result            # sufficient / cache hit (AC-2026-001, AC-2026-019)
if not result.query_vector:           return result            # spam / embed-down bypass (AC-2026-022, AC-2026-011)
if max == 0:                          return result            # kill switch (AC-2026-014)

best = result                                                   # best-seen (highest best_similarity)
current_query = result.canonical_query
prev_chunk_ids = {c["chunk_id"] for c in result.citations}     # empty on Layer-1 decline
for attempt in range(1, max + 1):                              # bounded — ADR-003 (AC-2026-013, AC-2026-017)
    try:
        rw = await AIGateway.rewrite_query(current_query)      # light tier — ADR-005 (AC-2026-007, AC-2026-010)
    except Exception:                 break                     # AC-2026-004, AC-2026-011 graceful
    new_q = rw.query.strip()
    if not new_q or not rw.keeps_subject or new_q.lower() == current_query.lower():
        break                                                   # no-progress / drift (AC-2026-012, AC-2026-015; BR-004)
    trace(attempt, new_q, guard="RETRY")                        # per-attempt model_trace (AC-2026-021)
    try:
        result = await search_and_retrieve(db, new_q, intent)  # FTS 500-word trunc reused (AC-2026-024)
    except Exception:                 break                     # mid-loop abort → best (AC-2026-018)
    if not result.declined:           return result            # rewrite succeeded (AC-2026-009)
    new_ids = {c["chunk_id"] for c in result.citations}
    if result.best_similarity <= best.best_similarity or new_ids == prev_chunk_ids:
        break                                                   # no-progress (AC-2026-015; BR-005)
    if result.best_similarity > best.best_similarity: best = result
    current_query, prev_chunk_ids = new_q, new_ids
return best                                                     # exhausted & insufficient → decline (AC-2026-016)
```

**Caller sequence (production path):** `retrieval_node` → `retrieve_with_retry` → (loop) → returns
`RetrievalResult` → `_build_result_dict` → state → `confidence_node` (unchanged) → `answer_node`.
Cache write still happens only after acceptance in the answer path, so intermediate rewrites are never
cached (AC-2026-023); the accepted result carries the final rewrite's `canonical_query`/`query_vector`.

## API Design

**openapi.yaml: N/A — no API contract change.** The loop is internal to the retrieval service.
`POST /query`'s request (`QueryRequest`) and response (`QueryResponse`) schemas are unchanged; the same
fields (`answer, declined, citations, best_similarity, …`) are returned whether the answer came from
attempt 0 or attempt N. No new endpoint, no new field, no status-code change. Exposing a
`retry_attempts` field would be scope creep (Non-Goal). Per `context/conventions.md`, FastAPI serves
the live schema at `/openapi.json`; no committed `openapi.yaml` is warranted for this change.

## DB Schema

_(unchanged — no new table, no migration.)_ Reuses existing `model_traces` (`ModelTrace`) for
per-attempt observability and `semantic_cache` for the final-only cache write. Per-attempt trace rows
add `attempt` (int) and `rewritten_query` (str) keys to the existing `metadata_` JSON column — no DDL.

## Error Mapping

Loop is below the HTTP boundary; it raises nothing to callers (mirrors current best-effort behavior).

| Failure inside loop | Handling | Surfaced as | AC |
|---|---|---|---|
| Rewriter LLM timeout/exception | `break`, return best-seen | current decline (`DECLINE_MESSAGE`, 200) | AC-2026-011 |
| Rewriter malformed/unparseable | fallback in `rewrite_query` → treated as failed attempt | decline | AC-2026-004 |
| `search_and_retrieve` raises mid-loop | `break`, return best-seen; no partial state | decline | AC-2026-018 |
| Embedding down (attempt 0) | `query_vector==[]` → bypass loop | current embed-unavailable message (200, declined) | AC-2026-011 |
| Spam (`is_valid=false`) | bypass loop | guard message (200, declined) | AC-2026-022 |
| Budget exhausted, still insufficient | return best-seen declined result | `DECLINE_MESSAGE` (200) | AC-2026-016 |

Existing HTTP mapping at `/query` (400/401/404/500 per conventions) is untouched.

## Edge Cases

| EC | Design handling |
|---|---|
| EC-001 empty/whitespace | `is_valid` heuristic in `normalize_query` declines before loop (attempt-0 bypass) |
| EC-002 >500-word / rewrite | each `search_and_retrieve` re-applies the 500-word FTS truncation (AC-2026-024) |
| EC-003 rewrite empty/stopwords | `new_q` empty → `break` (no-progress) |
| EC-004 first pass sufficient | `not result.declined` → return, loop never entered |
| EC-005 rewrite fixes it | attempt N `not declined` → return success (AC-2026-009) |
| EC-006 exhausted insufficient | return best-seen declined (AC-2026-016) |
| EC-007 rapid same-session turns | loop is per-turn; no cross-turn state (checkpointer unaffected) |
| EC-008 subject drift | `keeps_subject=false` → discard, `break` (BR-2026-004) |
| EC-009 no-progress (same ids / no sim gain) | id-set equality + `best_similarity` monotonic check → `break` |
| EC-010 rewriter down | exception → `break` → decline |
| EC-011 embed down mid-loop | `search_and_retrieve` returns `query_vector==[]`/declined → `break`/return |
| EC-012 cache hit | attempt 0 `not declined` → return, loop skipped |
| EC-013 max=0 | kill switch early return |
| EC-014 COMPARISON | ADR-004 early return, split handles recovery |
| EC-015 premium misconfig | `rewrite_query` hardcodes `economy-chat` — premium not selectable |

## Performance

Default cap 1 ⇒ worst-case added latency = 1 × (light-LLM rewrite + embed + hybrid search) on an
already-failing query only; sufficient/cache/spam paths add **zero** overhead (early returns).
No-progress and monotonic-`best_similarity` guards cap wasted attempts. Light tier (`qwen3:0.6b`,
`temperature=0`) keeps rewrite cost minimal and offline. Fully async — no blocking I/O (ruff `ASYNC`).

## Security

STRIDE pass (config `stride_analysis=auto`; concise model in `stride-threat-model.md`). Critical/High
addressed:
- **RISK-001 Runaway cost / DoS (HIGH, STRIDE-DoS).** Mitigated structurally: bounded for-loop
  (ADR-003), hard cap ≤2, default 1, kill switch 0, no-progress early stop, light tier only. Cost is
  provably `≤ N × (rewrite+search)`.
- **RISK-002 Prompt injection via rewrite (MEDIUM, STRIDE-Tampering/Info-Disclosure).** Rewrite output
  is used **only** as a parameterized retrieval query (SQLAlchemy `select()`, R-SEC-003) — **never
  executed**, never interpolated into SQL. Retrieval stays scoped to the merchant catalog; a crafted
  query cannot exfiltrate beyond catalog rows the customer could already query. `keeps_subject`
  constraint limits topic steering.
- **Logging (R-SEC-002).** Per-attempt traces store the rewritten *product-search* query + attempt# +
  guard decision only — no tokens, no secrets, no PII (email/phone). Rewrites are search text, not
  user PII.
- Secrets/config via `pydantic-settings` env (R-SEC-001) — `RAG_RETRY_MAX_ATTEMPTS` is a plain int.

## Risk Assessment

| Risk | → Mitigation |
|---|---|
| RISK-001 DoS/cost (HIGH) | bounded for-loop + cap + kill switch + no-progress (ADR-003) |
| RISK-002 prompt injection (MED) | rewrite = query only, parameterized, catalog-scoped (ADR-005 / §Security) |
| RISK-003 weak-answer regression (MED) | exhaustion = decline, not best-so-far answer (SPEC LOCK / AC-2026-016) |
| RISK-004 latency (MED) | default cap 1, light tier, early stops |
| RISK-005 single-pass test regressions (LOW) | kill switch `=0` byte-identical; regression tests; `answer_with_rag` refactor behind it |
| RISK-006 cache pollution (LOW) | cache write unchanged — only accepted final result cached (AC-2026-023) |
| **Open: subject-drift detection weak (G2)** | self-declared `keeps_subject` + prompt only; QA to probe drift cases; escalate to entity-check if it fails |

## Implementation Guide

**Recommended order** (data/config → service → orchestration → tests):
1. Add `RAG_RETRY_MAX_ATTEMPTS` to `core/config.py` (Week 3 block, `Field(default=1, ge=0, le=2)`).
2. Add `RewrittenQuery` model + `AIGateway.rewrite_query` in `services/ai.py` — **copy the
   `normalize_query` pattern** (`model="economy-chat"`, `response_format=RewrittenQuery`,
   `temperature=0`, heuristic pre-check, graceful `except` fallback with `keeps_subject=False`).
3. Add `retrieve_with_retry` in `services/rag/pipeline.py` (control flow above). Extend
   `_write_model_trace` (or a thin `_write_retry_trace`) to accept `attempt` + `rewritten_query` into
   `metadata_`.
4. Wire `retrieval_node` (`core/agent/nodes/retrieval.py`) and `queue_consumer.py` onto
   `retrieve_with_retry`; keep the COMPARISON split (it runs after, for COMPARISON only).
5. Refactor `answer_with_rag` to obtain retrieval via `retrieve_with_retry` (ADR-001) — keep its
   generation/trace/cache-write tail intact.
6. Tests: unit (gate classification table, no-progress, cap/kill-switch, drift discard), integration
   (real DB: recover-on-rewrite, exhaust→decline, COMPARISON no double-storm).

**Patterns to follow:** async-only (ruff `ASYNC`); best-effort try/except around every LLM/DB call
(mirror existing pipeline); never import `api/` from `services`/`core`; reuse `RetrievalResult` — do
not add fields to it.

**Gotchas:**
- `search_and_retrieve` already sets `declined` for THREE reasons — distinguish via `query_vector`
  presence (ADR-002 table) or you will rewrite spam / retry on a dead embed service.
- COMPARISON must early-return **before** the loop, else double retrieval (AC-2026-020).
- Cache write lives in the answer path, not in `retrieve_with_retry` — do not add a cache write inside
  the loop or intermediate rewrites leak into the cache (AC-2026-023).
- `rewrite_query` must hardcode `economy-chat`; do not thread a model param that could pass premium
  (AC-2026-010 / EC-015).
- Default cap 1 — do not raise the default; ops can set `=2` via env.
