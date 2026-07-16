# Sprint Retro — agentic-rag-retry-loop (ticket 2026, cr)

Date: 2026-07-16 · Rigor: full · Duration: 2026-07-15 → 2026-07-16

## Gate Compliance: 5/5 gates passed (1 loop-back, 1 infra blocker)

| Gate | Expected | Actual | Notes |
|------|----------|--------|-------|
| S1→S2 | Requirement Pack + ACs | ✅ | 24 ACs / 10 BRs / 15 edge cases, 0 TBD |
| 🔒 S2 SPEC LOCK | spec-auditor PASS + validate | ✅ | 0 blocker, convergence 3/3; 3 clarifications resolved at gate (cap=1, decline, ADDED) |
| 🔍 S3 DESIGN REVIEW | cross-artifact-audit 0 CRITICAL | ✅ | 24/24 AC coverage, convergence 3/3; ADR-001 blast-radius choice resolved at gate (plan A) |
| S4→S5 | tests + coverage | ✅ | 37/37 unit, new-code coverage 100%; 5 deviations logged & ruled |
| S5→S6 | QA GO + 0 Critical/High | ✅ (after 1 loop) | NO-GO → Bug #1 fix → retest 4/4 → GO |

**Loop-backs:** 1 × S5→S4 (Bug #1, cost 15×) — missing `db.rollback()` in `hybrid_search_rrf` FTS except; latent pre-existing gap turned real by this change's session-reuse. Fix = 1 line. No S→S2/S3 loop-backs (spec/design held).

**Infra blocker (not an SDLC cost):** S4 halted once — `sdlc.config.json paths.code_roots` was empty, developer write-fence didn't cover this repo's layout (`api/ core/ services/ models/ cli/`). Human populated it; ~1 wasted developer run.

## AI Performance
| Metric | Target | Actual |
|--------|--------|--------|
| AI-detectable bugs caught by AI | ≥90% | 1/1 (QA independently confirmed Bug #1 as real, env-independent, with live log evidence) |
| Logic bugs missed | 0 | 0 known |
| Spec adherence | 100% | 100% — 5 deviations all surfaced & ruled before/at gates, none unauthorized |
| New-code coverage | ≥80% | 100% (3 new functions) |

## 4Ls
**Liked**
- Kill-switch design (`RAG_RETRY_MAX_ATTEMPTS=0` byte-identical) made rollback plan trivial and de-risked the `/query` hot-path refactor.
- QA independent re-run caught that "environment-only" framing of the 2 integration failures was wrong — the dispute-by-evidence pattern worked exactly as intended.
- Narrow-scope subagent prompts (forbid re-explore, enumerate exact commands) cut late-phase runs to a fraction of early-phase cost (S6: 82k tokens vs S4 first run: 204k).

**Learned**
- Reusing an `AsyncSession` across N calls exposes any handler that swallows a DB exception without `rollback()` — audit session hygiene whenever adding multi-call reuse. (Already harvested to `memory/qa/` + `memory/developer/` by the roles — not duplicated here.)
- One-shot subagents re-pay the full exploration cost per spawn; killed runs re-pay it again. Total S4 cost ≈3 spawns. Mitigation that worked: assess-on-disk first (orchestrator), then spawn with a narrow "finish only" brief.

**Lacked**
- Project adoption never populated `paths.code_roots` → first S4 run burned on a deterministic fence block.
- No coverage gate wired (`pytest-cov` added ad-hoc at S4; no `--cov-fail-under` anywhere) — `stack.md` still flags this as owner-input.

**Longed for**
- Resumable/checkpointed subagent runs (a killed developer loses its context).
- A cheap pre-S4 "env preflight" (Ollama up? FTS config in test DB?) to separate env noise from real failures before they cost diagnosis time.

## Action Items (max 3)
1. [ ] [Owner/DevOps] Add "populate `sdlc.config.json paths.code_roots` for the repo's real layout" to the onboarder/adoption checklist — prevents the S4 fence block on every non-standard-layout project. [+1wk]
2. [ ] [Owner] Decide the coverage gate (D3): wire `--cov-fail-under=80` into `scripts/lint.sh` or CI, or explicitly accept manual coverage; update `stack.md`. [+1wk]
3. [ ] [Product/QA] Follow-up ticket: G2 subject-drift live probe once Ollama is up (self-declared `keeps_subject` false-negative rate); escalate to entity-overlap check only if prod shows drift. [+2wk]

## Memory harvest
Roles already wrote their own lessons during the pipeline (analyst/architect/developer ×2/qa — all `memory_writeback=appended`); de-dup check found no additional reusable lesson to add. Process items stay as Action Items above.
