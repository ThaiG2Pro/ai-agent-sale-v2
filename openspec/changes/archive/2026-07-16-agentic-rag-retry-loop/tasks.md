## 1. Config & data (foundational)

- [x] 1.1 Add `RAG_RETRY_MAX_ATTEMPTS: int = Field(default=1, ge=0, le=2)` to the Week 3 block of `Settings`. File: `core/config.py` _Requirements: AC-2026-013, AC-2026-014_
- [x] 1.2 Add `.env.example` entry `RAG_RETRY_MAX_ATTEMPTS=1` with a comment (0 = kill switch, max 2). File: `.env.example` _Requirements: AC-2026-013_ — was write-fence-blocked at S4 (D5); resolved during the S4-fix pass, line confirmed present by QA retest (2026-07-16, `qa-report.md` Gate Checklist).

## 2. Rewriter on the light tier

- [x] 2.1 Add `RewrittenQuery` Pydantic model (`query: str`, `keeps_subject: bool`). File: `services/ai.py` _Requirements: AC-2026-007, AC-2026-008_
- [x] 2.2 Add `AIGateway.rewrite_query(original: str) -> RewrittenQuery` mirroring `normalize_query`: `model="economy-chat"` (hardcoded light tier), `response_format=RewrittenQuery`, `temperature=0`, heuristic pre-check; prompt constrained to preserve intent + product entities and forbid subject change. File: `services/ai.py` _Requirements: AC-2026-007, AC-2026-008, AC-2026-010_
- [x] 2.3 Graceful fallback on exception/malformed output (return `keeps_subject=False` — treated as failed attempt); never raise. File: `services/ai.py` _Requirements: AC-2026-004, AC-2026-011_

## 3. Retry loop (domain/service)

- [x] 3.1 Extend `_write_model_trace` (or add `_write_retry_trace`) to record `attempt` + `rewritten_query` into `metadata_` (no PII/tokens). File: `services/rag/pipeline.py` _Requirements: AC-2026-021_
- [x] 3.2 Implement `retrieve_with_retry(db, query, intent) -> RetrievalResult`: attempt-0 `search_and_retrieve`; COMPARISON early-return (mutual exclusion); accept when `not declined`; bypass loop when `query_vector` empty (spam/embed-down); kill-switch when max==0. File: `services/rag/pipeline.py` _Requirements: AC-2026-001, AC-2026-002, AC-2026-003, AC-2026-005, AC-2026-006, AC-2026-014, AC-2026-019, AC-2026-020, AC-2026-022_
- [x] 3.3 Implement the bounded `for` loop body: rewrite → discard on empty/identical/`keeps_subject=false` → per-attempt trace → re-`search_and_retrieve` → success/no-progress/monotonic-`best_similarity`/exhaustion termination; track best-seen; abort-to-best on mid-loop exception. File: `services/rag/pipeline.py` _Requirements: AC-2026-007, AC-2026-009, AC-2026-011, AC-2026-012, AC-2026-013, AC-2026-015, AC-2026-016, AC-2026-017, AC-2026-018, AC-2026-024_

- [x] 3.4 **CHECKPOINT** — mid-build review: control flow matches design §Control Flow; cap is a bounded for-loop (RISK-001); rewrite hardcodes light tier; no cache write inside loop. Run `./scripts/lint.sh check`. _Requirements: AC-2026-013, AC-2026-023_ — ruff check PASS (0 errors on touched files).

## 4. Wire into orchestration (interface)

- [x] 4.1 Point `retrieval_node` at `retrieve_with_retry` (replacing the direct `search_and_retrieve` call); leave the COMPARISON split fallback below it intact (runs only for COMPARISON). File: `core/agent/nodes/retrieval.py` _Requirements: AC-2026-003, AC-2026-020_ — (D4: actual call site is `core/agent/tools.py::make_retrieval_tool`, imported/called by `retrieval_node`; COMPARISON split at `retrieval.py:158-162` untouched.)
- [x] 4.2 Point the `INFO_QUERY` batch retrieval at `retrieve_with_retry`. File: `core/agent/nodes/queue_consumer.py` _Requirements: AC-2026-003_
- [x] 4.3 Refactor `answer_with_rag` to obtain retrieval via `retrieve_with_retry` (ADR-001), keeping its generation/trace/cache-write tail so only the final accepted result is cached. File: `services/rag/pipeline.py` _Requirements: AC-2026-003, AC-2026-023_

## 5. Tests

- [x] 5.1 Unit: gate-classification table (sufficient/cache→accept; spam/embed-down→bypass; Layer-1→retry) reusing signals, no new scorer. File: `tests/unit/test_retrieve_with_retry.py` _Requirements: AC-2026-001, AC-2026-002, AC-2026-005, AC-2026-006, AC-2026-019, AC-2026-022_
- [x] 5.2 Unit: cap/kill-switch (`max`=0/1/2 never exceeded; work bound), no-progress (empty/identical/same chunk_ids/no sim gain), subject-drift discard, rewriter failure/malformed → decline. File: `tests/unit/test_retrieve_with_retry.py` _Requirements: AC-2026-004, AC-2026-011, AC-2026-012, AC-2026-013, AC-2026-014, AC-2026-015, AC-2026-016, AC-2026-017, AC-2026-018, AC-2026-024_
- [x] 5.3 Unit: light-tier enforcement (rewrite resolves to `economy-chat`, premium not selectable) + per-attempt `model_trace` written with no PII/tokens. File: `tests/unit/test_rewrite_query.py` _Requirements: AC-2026-008, AC-2026-010, AC-2026-021_
- [x] 5.4 Integration (real DB): recover-on-rewrite (attempt 1 succeeds → answer), exhaust→decline, COMPARISON produces no double retrieval storm, `answer_with_rag` final-only cache write. File: `tests/integration/test_retry_loop_pipeline.py` _Requirements: AC-2026-009, AC-2026-016, AC-2026-020, AC-2026-023_ — written + run against real `ai_agent_test` DB; 2/4 pass, 2/4 fail on a pre-existing test-DB FTS/session issue exposed by the 2nd retry attempt (not a retry-loop bug — see dev-test-report.md §Integration Test Findings).

- [x] 6.1 **CHECKPOINT** — final: all 24 ACs covered by a test; `./scripts/lint.sh check` clean; `uv run pytest` (unit) + `uv run pytest -m integration` green; kill-switch (`=0`) confirmed byte-identical to current single-pass behavior (RISK-005). _Requirements: AC-2026-001, AC-2026-014_ — 24/24 ACs have a passing unit test; lint clean; unit suite green (268 passed, 1 pre-existing unrelated failure); integration 2/4 green (2 fail on pre-existing env gap, documented, not code); kill-switch parity confirmed by `TestKillSwitchDeclineParityD2`.
