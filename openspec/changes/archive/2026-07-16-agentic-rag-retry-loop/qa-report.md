# S5 QA Report — 2026 (agentic-rag-retry-loop)
Date: 2026-07-15 (retest 2026-07-16)
QA Mode: Smart (dev-test-report.md present, 24/24 ACs claimed); focused re-verification + integration
root-cause + security audit; test_scope=module, RAG area only, per `_state.json`

## Retest Update — 2026-07-16 (ULTRA-NARROW pass, post S4-fix)
Fix applied by developer: 1-line `await db.rollback()` in `hybrid_search_rrf`'s FTS `except` block
(`services/rag/retrieval.py`). Confirmed via `git diff services/rag/retrieval.py` — exactly the
recommended fix, no other lines touched. `.env.example` now carries
`RAG_RETRY_MAX_ATTEMPTS=1  # 0 = kill switch, max 2` (task 1.2/D5 doc gap resolved).

Independently re-ran, live, at `test_scope=module`:
- `uv run pytest tests/integration/test_retry_loop_pipeline.py -q` → **4 passed** (was 2/4 pre-fix).
  Both previously-failing tests (`test_recover_on_rewrite_produces_answer`,
  `test_answer_with_rag_caches_only_final_accepted_query`) now pass — this re-verifies
  AC-2026-007/009/023 end-to-end against the real `ai_agent_test` DB, not just at the unit-mock level
  (closing the §4 "fragile-but-passing" note from the prior run).
- `uv run pytest tests/unit/test_retrieve_with_retry.py tests/unit/test_rewrite_query.py -q` →
  **37 passed** — unchanged, confirms the fix caused no regression.
- Only warnings observed: OTel `Failed to export traces to localhost:4317` (Phoenix collector down,
  cosmetic, unrelated to the fix) and a pre-existing aiohttp `DeprecationWarning`. No new warnings.

**Bug #1 status: RESOLVED-VERIFIED.** All 24/24 ACs now independently confirmed green end-to-end
(previously 21/24 green + 3/24 green-at-unit-only). Everything else from the prior session (security
STRIDE T1-T4 PASS, D2 kill-switch parity 4/4, 24/24 AC→test mapping) stands unchanged — not
re-explored this pass per the narrow retest scope.

## Gate Checklist
| Item | Result |
|------|--------|
| dev-test-report.md present | ✅ |
| Coverage ≥ threshold (new code) | ✅ 100% on the 3 new functions (`retrieve_with_retry`, `_write_retry_trace`, `rewrite_query`); whole-file 50-98% pre-existing-code-dominated (project has no `--cov-fail-under` gate — D3, pre-existing gap, not this change's to fix) |
| All required tasks `[x]` | ✅ 17/17 — task 1.2 (`.env.example` line) resolved this retest pass (line present, confirmed by grep) |
| Self-review log present | ✅ dev-test-report.md has self-verification commands, AC map, deviations |
| Integration smoke test (real run, not deferred) | ✅ ran against real `ai_agent_test` DB, real request/response captured; retest pass re-confirms 4/4 |
| `.env.example` ≥ 10 lines · README ≥ 10 lines · structured logging wired | ✅ 54 lines (incl. the resolved line) / ✅ 64 lines / ✅ `logfire.info/warn/error` used throughout `pipeline.py`, `ai.py` |
| Independently re-ran tests at `test_scope=module` | ✅ **4/4 integration + 37/37 unit** (post-fix retest, 2026-07-16) |

## Priority 1 — Bug #1 (RESOLVED-VERIFIED)
Original finding (2026-07-15): `hybrid_search_rrf`'s FTS exception handler
(`services/rag/retrieval.py`) caught the FTS exception but never called `await db.rollback()`.
Because `retrieve_with_retry` reuses the same `AsyncSession` across up to 3 `search_and_retrieve`
calls per request, an aborted-transaction state from attempt 0 cascaded into attempt 1 — live log
capture showed even L1/L2 cache lookups failing on attempt 1, confirming session-wide poisoning, not
just the FTS statement. Silently defeated AC-2026-009 (rewrite recovery) and AC-2026-023
(final-only cache write) whenever attempt 0 hit any FTS exception.

**Fix (2026-07-16)**: `await db.rollback()` added to the FTS `except` block, exactly as recommended.
Verified via `git diff` (1-line change, correct location) and by re-running the exact 2 previously-
failing integration tests live against `ai_agent_test` — both now pass, plus the full 37-test unit
regression suite stays green. Severity HIGH → **RESOLVED-VERIFIED**, no residual concern.

## Priority 2 — Security audit (STRIDE, `security.stride_analysis=auto`) — unchanged, stands from 2026-07-15
| # | Threat | Verdict |
|---|--------|---------|
| T1 DoS (unbounded loop) | **PASS** — structurally bounded: `core/config.py:65` `RAG_RETRY_MAX_ATTEMPTS: int = Field(default=1, ge=0, le=2)` + bounded `for` loop. |
| T2 Tampering/prompt-injection into retrieval | **PASS** — rewrite output bound as `:qtext` SQLAlchemy parameter, never string-concatenated. R-SEC-003 satisfied. |
| T3 Info disclosure via logs/traces | **PASS** — retry trace metadata has no `customer_id`, tokens, or raw errors. R-SEC-002 satisfied. |
| T4 Cost-tier elevation | **PASS** — `rewrite_query` hardcodes `model="economy-chat"`, no `model` param exposed. |

**Security verdict: PASS** — not re-run this pass (out of the ultra-narrow retest scope); no code
changed in this area since the last audit, so the prior PASS stands.

## Priority 3 — G2 subject-drift (carried, unresolved, non-blocking)
Unchanged from 2026-07-15: could not live-probe (Ollama down). Residual risk, no KPI weight,
recommend a follow-up ticket. Not re-probed this retest pass (out of scope, unrelated to Bug #1).

## Priority 4 — Kill-switch parity (D2) — unchanged, stands from 2026-07-15
4/4 `TestKillSwitchDeclineParityD2` independently re-run and passed on 2026-07-15; not re-run this
pass (unrelated to the Bug #1 fix, no code in this path changed).

## Priority 5 — AC→test mapping — updated
All 24/24 ACs now independently verified end-to-end green (previously 21/24 + 3/24 unit-only). See
`qa/testcases.md` for the full updated mapping — TC-06 and TC-20 flipped from 🟡/❌ to ✅.

## Bug List
| # | Title | AC-ID | Severity | Classification | RCA Phase | Status |
|---|-------|-------|----------|----------------|-----------|--------|
| 1 | `hybrid_search_rrf`'s FTS exception handler didn't roll back the shared `AsyncSession` before `retrieve_with_retry` reused it — poisoned the entire session, silently defeating retry recovery | AC-2026-009, AC-2026-018, AC-2026-023 | HIGH | [AI-DETECTABLE] | S4 | **RESOLVED-VERIFIED 2026-07-16** |

## AC Coverage Summary
- Total ACs: 24
- Covered by Dev (unit tests): 24/24 (100%, all green)
- Independently verified by QA end-to-end: **24/24 (100%)** as of the 2026-07-16 retest (previously
  21/24 fully green + 3/24 unit-only, blocked by Bug #1 — now resolved)
- Not covered: 0 — 1 residual risk (G2 subject-drift false-negative rate) remains untestable this
  session (Ollama down); not an AC gap, a live-model-dependent probe gap, no KPI weight

## Dependency Vulnerability Audit
Unchanged from 2026-07-15: no new third-party runtime dependency added (`pytest-cov` is dev-only).
No HIGH/CRITICAL findings. Clean.

## Decision: GO
**Reason**: Bug #1 (the sole open blocker from the prior NO-GO) is RESOLVED-VERIFIED — independently
re-confirmed by live re-run of the exact 2 previously-failing integration tests (now 4/4) plus the
full 37-test unit regression suite (unchanged, no new failures). 0 Critical/High bugs open. All 24
ACs independently verified end-to-end. Security STRIDE audit PASS (stands from prior session, no
code changed in that area). Dependency audit clean. Per the S5 gate ("0 Critical/High bugs open +
all ACs verified + regression met + deps clean"), this change is GO for S6.

## Deploy risks / residual items (non-blocking, carry forward)
- **G2 subject-drift** (residual, no KPI): `keeps_subject` is a self-declared model flag, no
  independent NER/entity check. Recommend a follow-up ticket if false negatives are observed in
  production. Not a release blocker.
- Fragile-but-passing item from the prior session (no-progress guard triggering on "both attempts
  zero chunks" rather than a literal identical-chunk-set) — logic is unchanged by the Bug #1 fix,
  still worth a light look in a future pass, not a retest blocker now since the same 4/4 integration
  suite exercising this path passes.
