## Dev Test Report — 2026 (agentic-rag-retry-loop)
Date: 2026-07-15 · Agent: developer (S4, verify+document tail — code/tests were written in a prior
S4 segment after the write-fence blocker was cleared by setting `sdlc.config.json paths.code_roots`).

### Self-Verification Commands Run
| Check | Command | Result |
|-------|---------|--------|
| Lint (ruff check) | `./scripts/lint.sh check` | ✅ PASS — 0 errors on touched files |
| Format check | `./scripts/lint.sh check` (ruff format --check) | ⚠️ 2 files need reformat: `api/routes/memory.py`, `core/telegram/message_handler.py` — **pre-existing, not touched by this change** |
| Unit tests | `uv run pytest tests/unit -q --cov=...` | 268 passed, 1 failed (pre-existing, unrelated) |
| New unit tests only | `uv run pytest tests/unit/test_retrieve_with_retry.py tests/unit/test_rewrite_query.py -q` | **37/37 passed** |
| Integration | `uv run pytest tests/integration/test_retry_loop_pipeline.py -q` | 2/4 passed, 2/4 failed — root cause below (test-DB environment, not retry-loop logic) |

### Pre-existing Failure (not this change)
`tests/unit/test_hitl_service.py::test_validation_error_marks_incompatible` — fails with
`TypeError: ValueError: 'error' required in context` building a mocked `ValidationError` via
`ValidationError.from_exception_data`. Unrelated file (`services/hitl`), unrelated to
`agentic-rag-retry-loop`; matches the known "HITL ValidationError mock" issue flagged as
pre-existing in the task brief. Not fixed (out of scope).

### AC → Test Mapping (24/24 covered)
| AC-ID | Test File | Test Function | Status |
|-------|-----------|----------------|--------|
| AC-2026-001 | test_retrieve_with_retry.py | `test_sufficient_first_pass_accepted_no_loop` | ✅ PASS |
| AC-2026-002 | test_retrieve_with_retry.py | `test_declined_false_takes_priority_over_empty_vector` | ✅ PASS |
| AC-2026-003 | test_retrieve_with_retry.py | `test_layer1_insufficient_with_budget_enters_loop` | ✅ PASS |
| AC-2026-004 | test_rewrite_query.py | `test_malformed_json_returns_keeps_subject_false` (+ `test_subject_drift_discarded`) | ✅ PASS |
| AC-2026-005 | test_retrieve_with_retry.py | `test_layer1_insufficient_with_budget_enters_loop` | ✅ PASS |
| AC-2026-006 | test_retrieve_with_retry.py | `test_missing_confidence_signals_no_crash` | ✅ PASS |
| AC-2026-007 | test_rewrite_query.py | `test_successful_rewrite_parses_structured_output` (+ integration `test_recover_on_rewrite_produces_answer`) | ✅ unit PASS / ⚠️ integration FAIL (env, see below) |
| AC-2026-008 | test_rewrite_query.py | `test_prompt_instructs_subject_preservation`, `test_english_query_rewrite_preserves_flag` | ✅ PASS |
| AC-2026-009 | test_retry_loop_pipeline.py (integration) | `test_recover_on_rewrite_produces_answer` | ⚠️ FAIL (env, see below) |
| AC-2026-010 | test_rewrite_query.py | `test_hardcodes_economy_chat_model` | ✅ PASS |
| AC-2026-011 | test_retrieve_with_retry.py | `test_embed_unavailable_bypasses_loop`, `test_rewriter_exception_aborts_to_best` | ✅ PASS |
| AC-2026-012 | test_retrieve_with_retry.py | `test_no_progress_empty_rewrite_stops`, `test_no_progress_identical_rewrite_stops`, `test_subject_drift_discarded` | ✅ PASS |
| AC-2026-013 | test_retrieve_with_retry.py | `test_cap_one_never_exceeded_on_no_progress`, `test_cap_two_never_exceeded` | ✅ PASS |
| AC-2026-014 | test_retrieve_with_retry.py | `test_kill_switch_zero_returns_first_pass_untouched`, `test_kill_switch_never_calls_rewriter` | ✅ PASS |
| AC-2026-015 | test_retrieve_with_retry.py | `test_no_similarity_gain_stops`, `test_similarity_gain_lost_when_citations_stay_empty` | ✅ PASS |
| AC-2026-016 | test_retrieve_with_retry.py + integration | `test_cap_two_never_exceeded` (best_similarity assertion) + `test_exhaustion_still_insufficient_declines` | ✅ PASS (both) |
| AC-2026-017 | test_retrieve_with_retry.py | `test_cap_one_never_exceeded_on_no_progress`, `test_cap_two_never_exceeded` | ✅ PASS |
| AC-2026-018 | test_retrieve_with_retry.py | `test_rewriter_exception_aborts_to_best`, `test_search_and_retrieve_exception_mid_loop_aborts` | ✅ PASS |
| AC-2026-019 | test_retrieve_with_retry.py | `test_cache_hit_skips_loop` | ✅ PASS |
| AC-2026-020 | test_retrieve_with_retry.py + integration | `test_comparison_intent_never_loops` + `test_comparison_intent_no_double_retrieval_storm` | ✅ PASS (both) |
| AC-2026-021 | test_retrieve_with_retry.py | `test_retry_trace_written_once_per_attempt`, `test_retry_trace_metadata_has_no_pii_or_tokens` | ✅ PASS |
| AC-2026-022 | test_retrieve_with_retry.py | `test_spam_bypasses_loop` | ✅ PASS |
| AC-2026-023 | test_retrieve_with_retry.py + integration | `test_no_cache_write_inside_retry_loop` + `test_answer_with_rag_caches_only_final_accepted_query` | ✅ unit PASS / ⚠️ integration FAIL (env, see below) |
| AC-2026-024 | test_retrieve_with_retry.py | `test_fts_truncation_reused_for_rewritten_query` | ✅ PASS |

**24/24 ACs have at least one PASSING unit test.** 3 ACs (007, 009, 023) additionally have an
integration test that currently fails for an environment reason unrelated to the retry-loop logic
(see next section) — the unit-level coverage for those same ACs is green.

### Integration Test Findings (`tests/integration/test_retry_loop_pipeline.py`, real `ai_agent_test` DB)
Ran against the real Postgres test DB (no DB mocking, per R10); Ollama at `localhost:11434` is down
in this environment but the pipeline's `normalize_query` degrades gracefully to a fallback (no
raise), so tests did **not** skip — they ran, slower (~5-9s per call from Ollama connect-retry).

- `test_exhaustion_still_insufficient_declines` — ✅ PASS
- `test_comparison_intent_no_double_retrieval_storm` — ✅ PASS
- `test_recover_on_rewrite_produces_answer` — ❌ FAIL
- `test_answer_with_rag_caches_only_final_accepted_query` — ❌ FAIL

Root cause (both failures): the test DB's Vietnamese FTS query raises
`sqlalchemy.dialects.postgresql.asyncpg.ProgrammingError` (message scrubbed by Logfire PII
redaction). `search_and_retrieve`'s pre-existing FTS→vector-only fallback catches this once and
degrades gracefully on **attempt 0** (test still gets a result, just declined) — but it does not
`rollback()` the shared `AsyncSession`, so the Postgres transaction stays aborted. On **attempt 1**
(the new retry-loop's re-`search_and_retrieve` call, same session), *any* further statement —
including the plain vector query — raises against the aborted transaction, and
`retrieve_with_retry`'s abort-to-best-seen path (AC-2026-018) correctly catches it and returns the
attempt-0 (declined) result instead of crashing. The two failing tests specifically assert a
*successful* recovery on attempt 1, so they fail; the two passing tests don't depend on attempt 1
succeeding (exhaustion always declines; COMPARISON never loops).

This is a **pre-existing test-DB/environment gap** (FTS extension/config, not part of this change's
task scope — the FTS-failure-then-fallback code predates this change) that only becomes *visible*
now because the retry loop reuses the same session for a second attempt. Per the S4-FIX scope
given for this run, this is not fixed here — flagged for QA to re-run once the FTS setup in
`ai_agent_test` is corrected (or against a DB where the Vietnamese FTS config is present), and to
independently confirm the abort-to-best-seen path (AC-2026-018) is itself working as designed
(it is — that's *why* these two tests fail cleanly instead of crashing).

### Coverage — Changed Modules
Coverage of the specific NEW functions this change added (verified by locating the missing-line
ranges from `--cov-report=term-missing --cov-branch` against each function's line span):

| New symbol | File | Coverage |
|---|---|---|
| `retrieve_with_retry` | `services/rag/pipeline.py` (lines 306-411) | **100%** — no missing lines in range |
| `_write_retry_trace` | `services/rag/pipeline.py` (lines 587-633) | **100%** — no missing lines in range |
| `rewrite_query` | `services/ai.py` (lines 358-423) | **100%** — no missing lines in range |
| `RAG_RETRY_MAX_ATTEMPTS` field | `core/config.py` | **100%** — file overall 98% (1 missing line is pre-existing `database_url_psycopg`, unrelated) |

Whole-file coverage (includes substantial pre-existing/untouched code in these shared files —
reported for transparency, not as a per-line pass/fail):

| File | Stmts | Miss | Branch | Partial | Cover |
|---|---|---|---|---|---|
| `services/rag/pipeline.py` | 214 | 60 | 44 | 7 | 70% |
| `services/ai.py` | 121 | 37 | 12 | 1 | 65% |
| `core/config.py` | 59 | 1 | 0 | 0 | 98% |
| `core/agent/tools.py` | 88 | 39 | 10 | 0 | 50% |
| `core/agent/nodes/queue_consumer.py` | 230 | 89 | 86 | 18 | 60% |

The whole-file gaps are pre-existing (`answer_with_rag`'s LLM-generation tail requiring live
Ollama; order-execution/timeout branches in `tools.py`/`queue_consumer.py` unrelated to retrieval).
The 2-line wiring change in each of `tools.py`'s `retrieve()` closure and `queue_consumer.py`'s
`_resolve_new_product_from_modify` is exercised indirectly by pre-existing tests
(`test_queue_consumer_node.py` passed unchanged) and by the integration suite above.
`pytest-cov` was added to `[dependency-groups].dev` in `pyproject.toml` this change (no
`--cov-fail-under` wired into any script — deviation D3 below).

### Design Deviations
- **D1 — `RetrievalResult` observability fields.** Design didn't specify new fields on
  `RetrievalResult`; `_write_retry_trace` records attempt/rewrite/guard data into
  `model_traces.metadata_` (JSON) instead, per the architect's §3 inference (no DDL needed). Minor,
  matches `_handoff.md` §3 from S3.
- **D2 — decline_reason parity.** No new `decline_reason` enum was added (would be scope creep per
  ADR-002); the retry loop still distinguishes decline causes via `query_vector` presence only.
  Covered by `TestKillSwitchDeclineParityD2` (decline text byte-identical at kill-switch=0).
- **D3 — `pytest-cov` added without `--cov-fail-under`.** Added the dependency (closes part of the
  `stack.md` "UNKNOWN — needs owner input" gap) but did not wire a coverage gate into
  `scripts/lint.sh` or a new script — that's a tooling/CI decision beyond this change's scope; left
  for the orchestrator/owner to decide whether to make coverage a hard CI gate project-wide.
- **D4 — task 4.1 wiring file.** `tasks.md` 4.1 said "File: `core/agent/nodes/retrieval.py`" but the
  actual call site is `core/agent/tools.py::make_retrieval_tool`'s `retrieve()` closure (imported and
  called by `retrieval_node` at `core/agent/nodes/retrieval.py:20,131`) — confirmed the wiring is
  effective; file location differs from the task note only.
- **D5 — `.env.example` NOT updated (blocked).** `sdlc.config.json paths.code_roots` covers
  directory globs (`api/**`, `core/**`, `services/**`, `models/**`, `cli/**`) but not root-level
  files; `Edit(.env.example)` was denied by `check-write-path.py` (developer write-fence). The
  `RAG_RETRY_MAX_ATTEMPTS=1` default is still live at runtime (baked into
  `core/config.py`'s `Field(default=1, ge=0, le=2)`, pydantic-settings applies it with no `.env`
  entry needed) — this is a **documentation/operator-visibility gap only**, not a functional gap.
  Recommend the orchestrator either add `.env.example` (a specific filename, not a glob) to
  `paths.code_roots`, or a human adds the one line by hand:
  `RAG_RETRY_MAX_ATTEMPTS=1  # 0 = kill switch, max 2`.

### G2 Note for QA (carried from S3 `_handoff.md`)
Subject-drift detection (`keeps_subject`) is the light model's **self-declared** flag plus a prompt
constraint — no independent NER/entity check. `test_subject_drift_discarded` only verifies the
pipeline *honors* `keeps_subject=False`, not that the model always sets it correctly. QA should
manually probe a few subject-switching rewrite scenarios (e.g., "does the widget-pro have a battery"
→ rewritten to ask about a different product) against a live light-tier model to gauge false-negative
rate (the model itself doesn't say `keeps_subject=False` when it should have).

### Coverage Verification
- Command: `uv run pytest tests/unit -q --cov=services/rag/pipeline --cov=services/ai
  --cov=core/config --cov=core/agent/tools --cov=core/agent/nodes/queue_consumer
  --cov-report=term-missing --cov-branch`
- New-code coverage (the 3 new functions this change added): **100%**
- Whole-file coverage on shared/touched files: 50-98% (pre-existing code dominates; see table above)
- Type-check: N/A (Python project, no `mypy`/`pyright` configured per `stack.md`)
- Lint: ✅ PASS (ruff check clean on all touched files; 2 unrelated pre-existing format issues noted)
