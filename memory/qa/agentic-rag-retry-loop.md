# QA memory — agentic-rag-retry-loop

## 2026-07-15 — agentic-rag-retry-loop: session-reuse across a retry loop exposes latent missing-rollback bugs — always audit exception handlers on the shared session's call path

**Lesson (reusable):** When a change introduces a loop/retry pattern that reuses the SAME
`AsyncSession` across multiple sequential calls to the same DB-touching function (here
`retrieve_with_retry` calling `search_and_retrieve` up to N times per request), any pre-existing
`except` block on that call path that swallows an exception WITHOUT `await db.rollback()` becomes a
real, reproducible defect — even though it was harmless before (when the function was only called
once per request/session lifetime). Symptom to watch for in logs: not just the retried statement
failing, but UNRELATED statements on the same session (e.g. cache lookups) also failing right after —
that is the tell that the whole session is poisoned, not just the retried call. Dev-level unit tests
with mocked sessions will NOT catch this (mocks don't model transaction-abort semantics); it only
surfaces on a live integration re-run against a real Postgres test DB. **Checklist item for future
QA passes**: whenever a change turns a "call once" pattern into a "call N times reusing one session"
pattern (retry loops, batch loops, pagination loops), explicitly grep every `except` block reachable
from that session for a missing `rollback()` before accepting integration test failures as
"environment-only" — re-run live and read the actual log sequence rather than trusting the dev's own
RCA.

## 2026-07-16 — agentic-rag-retry-loop: retest verification — confirm the fix is EXACTLY the recommended diff before re-running tests

**Lesson (reusable):** On a bug-fix retest pass, run `git diff <file>` on the specific file named in
the bug report FIRST, before re-running any tests. It is cheap (one command) and catches two failure
modes early: (1) the dev fixed a different file/line than recommended (scope drift), or (2) the "fix"
bundled unrelated changes that need separate scrutiny. Here the diff was exactly the 1-line
recommended fix (`await db.rollback()` in the FTS except block) — confirming the retest could
proceed straight to re-running the exact previously-failing integration tests without re-deriving the
RCA or re-reading the whole file.
