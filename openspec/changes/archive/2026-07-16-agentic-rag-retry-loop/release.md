---
name: release-template
description: >
  Template for {CHANGE_DIR}/release.md — the S6 release artifact the developer writes before
  `openspec archive`. Contains: release notes (ref AC-IDs), migration checklist, rollback plan,
  post-deploy smoke test, deploy strategy.
---

# Release — 2026 (agentic-rag-retry-loop)
Date: 2026-07-16
Deploy strategy: direct (standard rolling deploy, env-flag guarded feature — no canary needed;
`RAG_RETRY_MAX_ATTEMPTS` gives an instant kill switch if anything looks wrong post-deploy)

## Release Notes
**Features**
- Agentic RAG retry loop: `retrieve_with_retry` wraps `search_and_retrieve` with a bounded
  self-eval → rewrite → retry cycle (AC-2026-001..006, AC-2026-018). On a low-confidence/empty
  result, the light-tier model (`AIGateway.rewrite_query`, hardcoded `economy-chat`, AC-2026-010)
  rewrites the query and the pipeline retries, up to `RAG_RETRY_MAX_ATTEMPTS` total attempts
  (bounded `for` loop, RISK-001).
- New config `RAG_RETRY_MAX_ATTEMPTS: int` (`core/config.py`, default `1`, `ge=0, le=2`) — `0` is a
  kill switch, byte-identical to pre-change behavior (RISK-005, verified by
  `TestKillSwitchDeclineParityD2`, 4/4 pass).
- COMPARISON queries early-return before the loop, no retry (AC-2026-020). No cache write happens
  mid-loop — only the final accepted query/answer is cached (AC-2026-023).
- Retry observability recorded into `model_traces.metadata_` (`attempt`, rewrite text, guard
  decisions) — no new table/column (D1).
- All 24/24 ACs independently verified end-to-end by QA (2026-07-16 retest, GO).

**Bug fixes**
- `hybrid_search_rrf`'s FTS exception handler now calls `await db.rollback()` before returning
  (`services/rag/retrieval.py`) — previously an aborted-transaction state from attempt 0 poisoned
  the shared `AsyncSession` and silently defeated retry recovery on attempt 1+
  (AC-2026-007, AC-2026-009, AC-2026-023; QA Bug #1, HIGH, RESOLVED-VERIFIED).

**Breaking changes**
- None. No API contract change (`POST /query` response shape unchanged); purely internal
  retrieval-pipeline behavior plus one new optional env var with a safe default.

## Migration Checklist
**NONE.** No DDL, no new tables/columns — this change is pure application logic + one new
`Settings` field (`RAG_RETRY_MAX_ATTEMPTS`, pydantic-settings default `1`, no `.env` entry
required to take effect). `.env.example` documents it for operator visibility
(`RAG_RETRY_MAX_ATTEMPTS=1  # 0 = kill switch, max 2`).

## Rollback Plan
1. **Primary (preferred, no deploy)**: set `RAG_RETRY_MAX_ATTEMPTS=0` in the environment and
   restart the app. This is a structural kill switch — the loop short-circuits to exactly one
   attempt, byte-identical to pre-change decline/answer text (verified by D2 parity tests). Use
   this first if retry behavior looks wrong in production.
2. **Secondary (code revert)**: if the kill switch is insufficient (e.g. the Bug #1 rollback fix
   itself needs to go too), `git revert` the merge commit for
   `cr/2026-agentic-rag-retry-loop` and redeploy. No down-migration needed (no schema change).
3. **Confirm recovery**: re-run the post-deploy smoke test below; check `model_traces` stops
   showing `attempt > 0` entries after the kill switch/revert.

## Post-Deploy Smoke Test
- [ ] Normal, well-formed query → `POST /query` returns a grounded answer with citations
      (`declined=false`), `model_traces.metadata_.attempt = 0` (no retry needed).
- [ ] Poorly-phrased/ambiguous query → answer still returns; check
      `model_traces.metadata_.attempt >= 1` and a `rewritten_query` present, confirming the retry
      path fired (AC-2026-007/009).
- [ ] Spam / nonsense query → response is `declined=true`, and `model_traces` shows **no** retry
      attempt (query_vector-absent decline path must not trigger a rewrite — D2/ADR-002).
- [ ] Health endpoint green; error rate and p95 latency within normal budget for 15-30 min post
      deploy (retry adds at most `RAG_RETRY_MAX_ATTEMPTS` extra light-tier LLM calls on the
      low-confidence path only, so a latency bump on that slice is expected, not an error).
- [ ] Residual watch item (non-blocking): **G2 subject-drift** — `keeps_subject` is a self-declared
      model flag with no independent NER check, unresolved because Ollama was down during S5.
      Recommend a human re-probe once Ollama is up (a couple of subject-switching rewrite prompts,
      see `_handoff.md` §2) and file a follow-up ticket if false negatives show up.

## Archive
- [x] `openspec archive "agentic-rag-retry-loop"` run — spec deltas merged into
      `openspec/specs/rag-pipeline/spec.md` (first living spec for this capability), change moved
      to `openspec/changes/archive/`.
- [ ] `_state.json.deploy_status` initialized (`dev`/`stg`/`master` = `pending`) — updated later,
      out-of-band, as each real promotion completes
      (`state-set --set deploy_status.<env>=pass|fail`). Not a gate — a breadcrumb.

## If Rejected After Archive (Revert Playbook)
Archive already ran before this reaches dev/stg/master — a bug caught downstream does NOT mean
re-opening this change:
- **Forward-fixable** (bug found in dev/stg, or in master but no rollback needed): open a new
  `bugfix` (or `hotfix` if already in master) pipeline. Do not touch this archived change or
  hand-edit `openspec/specs/rag-pipeline/spec.md`.
- **Real rollback** (the deploy itself gets reverted): `git revert <archive-merge-commit>` — undoes
  the code AND the spec fold atomically. Never hand-edit the living spec back; let `git revert` do
  both, then re-run the fix as its own pipeline.
