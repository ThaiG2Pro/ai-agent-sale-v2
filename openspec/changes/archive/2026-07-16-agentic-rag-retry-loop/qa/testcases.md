# QA Test Cases — agentic-rag-retry-loop (ticket 2026)
Date: 2026-07-15 (retest pass: 2026-07-16) · QA Mode: Smart (dev-test-report.md existed, 24/24 ACs
claimed covered) · test_scope: module (RAG area only, per `_state.json`)

Legend: Result = ✅ independently re-run and passed this session · 🟡 traced/read-verified (not
independently executed by QA, e.g. covered only by integration test that fails for env reasons) ·
❌ failed.

| TC-ID | AC-ID | Scenario | How to verify | Priority | Result |
|-------|-------|----------|----------------|----------|--------|
| TC-01 | AC-2026-001 | Sufficient first pass accepted, no loop entered | `test_sufficient_first_pass_accepted_no_loop` — assert `search_and_retrieve` called once, `rewrite_query` never called | High | ✅ |
| TC-02 | AC-2026-002 | `declined=False` takes priority over empty `query_vector` (cache-hit shape) | `test_declined_false_takes_priority_over_empty_vector` | Med | ✅ |
| TC-03 | AC-2026-003 / 005 | Layer-1 insufficient + budget>0 enters loop | `test_layer1_insufficient_with_budget_enters_loop` | High | ✅ |
| TC-04 | AC-2026-004 | Malformed/unparseable rewrite output never raises, degrades to `keeps_subject=False` | `test_malformed_json_returns_keeps_subject_false` + `test_subject_drift_discarded` | High | ✅ |
| TC-05 | AC-2026-006 | Missing confidence signals (`best_similarity=0.0`) treated as insufficient, no crash | `test_missing_confidence_signals_no_crash` | High | ✅ |
| TC-06 | AC-2026-007 / 009 | Insufficient retrieval → light-tier rewrite → re-retrieval; successful rewrite recovers an answer | Unit: `test_successful_rewrite_parses_structured_output` ✅. Integration: `test_recover_on_rewrite_produces_answer` — **RESOLVED-VERIFIED 2026-07-16**: `await db.rollback()` added to `hybrid_search_rrf`'s FTS except block; re-ran live, now PASSES (part of the 4/4 integration re-run) | Critical | ✅ |
| TC-07 | AC-2026-008 | Rewrite preserves intent/entity across VI/EN | `test_prompt_instructs_subject_preservation`, `test_english_query_rewrite_preserves_flag` | High | ✅ |
| TC-08 | AC-2026-010 / BR-003 | Rewrite always resolves to `economy-chat` (light tier), never premium, no model param exposed | `test_hardcodes_economy_chat_model`; source-read `rewrite_query` signature (no `model` param) | Critical (security T4) | ✅ |
| TC-09 | AC-2026-011 / 018 | Mid-loop rewrite exception aborts to best-seen, no crash | `test_rewriter_exception_aborts_to_best`, `test_search_and_retrieve_exception_mid_loop_aborts` | Critical | ✅ (Bug #1 session-poisoning path now fixed, see TC-06/TC-20) |
| TC-10 | AC-2026-012 / BR-004/005 | No-progress (empty/identical rewrite or subject drift) stops loop early | `test_no_progress_empty_rewrite_stops`, `test_no_progress_identical_rewrite_stops`, `test_subject_drift_discarded` | High | ✅ |
| TC-11 | AC-2026-013 / 017 / BR-001 | Loop never exceeds configured cap (N=1, N=2) | `test_cap_one_never_exceeded_on_no_progress`, `test_cap_two_never_exceeded` | Critical (DoS/cost, T1) | ✅ |
| TC-12 | AC-2026-014 / BR-007 | `RAG_RETRY_MAX_ATTEMPTS=0` = kill switch, byte-identical to static pipeline | `test_kill_switch_zero_returns_first_pass_untouched`, `test_kill_switch_never_calls_rewriter` + `TestKillSwitchDeclineParityD2` (3 decline-text tests) | Critical | ✅ (independently re-run, 4/4 passed) |
| TC-13 | AC-2026-015 | No similarity gain / empty citations stay empty → stop | `test_no_similarity_gain_stops`, `test_similarity_gain_lost_when_citations_stay_empty` | High | ✅ |
| TC-14 | AC-2026-016 | Budget exhausted, still insufficient → current decline behavior (no bar-lowering) | `test_cap_two_never_exceeded` (best_similarity assertion) + integration `test_exhaustion_still_insufficient_declines` | High | ✅ (unit + integration both re-run and passed) |
| TC-15 | AC-2026-018 | Mid-loop failure aborts cleanly, no partial/corrupt state | `test_rewriter_exception_aborts_to_best`, `test_search_and_retrieve_exception_mid_loop_aborts` — plus QA's own live-DB reproduction of the FTS/session-poisoning path (Bug #1, now fixed) | Critical | ✅ |
| TC-16 | AC-2026-019 / BR-006 | Cache hit (L1/L2) skips loop entirely | `test_cache_hit_skips_loop` | Med | ✅ |
| TC-17 | AC-2026-020 / INT-006 | COMPARISON intent never loops (mutual exclusion with split fallback) | `test_comparison_intent_never_loops` + integration `test_comparison_intent_no_double_retrieval_storm` | High | ✅ (unit + integration both re-run and passed) |
| TC-18 | AC-2026-021 / BR-008 | Each retry attempt traced; trace has no PII/tokens | `test_retry_trace_written_once_per_attempt`, `test_retry_trace_metadata_has_no_pii_or_tokens` + source-read `_write_retry_trace` metadata keys | Critical (security T3, R-SEC-002) | ✅ |
| TC-19 | AC-2026-022 / BR-006 | Spam (`is_valid=false`) declined before any rewrite | `test_spam_bypasses_loop` | High | ✅ |
| TC-20 | AC-2026-023 | Only the final accepted result is cached, not intermediate rewrites | Unit: `test_no_cache_write_inside_retry_loop` ✅. Integration: `test_answer_with_rag_caches_only_final_accepted_query` — **RESOLVED-VERIFIED 2026-07-16**: same fix as TC-06, re-ran live, now PASSES | Critical | ✅ |
| TC-21 | AC-2026-024 / BR-010 | FTS 500-word truncation re-applied to rewritten query | `test_fts_truncation_reused_for_rewritten_query` | Med | ✅ |
| TC-22 (security) | RISK-002/T2 | Rewrite output is used ONLY as a parameterized retrieval query, never executed/interpolated | Source-read: `search_and_retrieve(db, new_query, intent)` → `hybrid_search_rrf` binds `new_query` as `:qtext` SQLAlchemy bind param (never string-concatenated); confirmed no `eval`/exec path | Critical (R-SEC-003) | ✅ |
| TC-23 (security) | RISK-001/T1 | Retry budget structurally bounded (config `ge=0,le=2` + bounded `for` loop, not recursion) | Source-read `core/config.py:65` (`Field(default=1, ge=0, le=2)`) + `retrieve_with_retry`'s `for attempt in range(1, max_attempts+1)` | Critical (DoS) | ✅ |
| TC-24 (G2, carried) | BR-004 | Subject-drift false-negative probe against a LIVE light-tier model | Attempted — **Ollama unreachable at localhost:11434 in this environment** (`curl` connect failed); could not live-probe. Only the self-declared-flag unit path (TC-10) was verifiable | Med (residual risk, no KPI — SPEC-UNCLEAR-adjacent) | 🟡 not executable this session |

## Coverage Summary
- 24/24 functional ACs have ≥1 row above; **24/24 independently re-run and green** as of the
  2026-07-16 retest pass (Bug #1 fix verified end-to-end, TC-06/TC-20 flipped ✅).
- 2 security-specific test cases (TC-22, TC-23) added by QA (not in dev's AC-tagged suite, since
  they map to the STRIDE threat model rather than a spec AC) — both PASS by source inspection.
- 1 test case (TC-24, G2 carried risk) could not be executed — Ollama unreachable this session;
  carried forward as a non-blocking residual risk (no KPI weight).

## Retest Pass — 2026-07-16
Fix applied: 1-line `await db.rollback()` added to `hybrid_search_rrf`'s FTS `except` block
(`services/rag/retrieval.py`) — confirmed via `git diff` (exactly the recommended fix, no other
changes). Independently re-ran:
- `tests/integration/test_retry_loop_pipeline.py` → **4/4 passed** (was 2/4).
- `tests/unit/test_retrieve_with_retry.py` + `tests/unit/test_rewrite_query.py` → **37/37 passed**
  (unchanged, regression-clean).
Bug #1 status: **RESOLVED-VERIFIED**. All 24 ACs now green end-to-end.
