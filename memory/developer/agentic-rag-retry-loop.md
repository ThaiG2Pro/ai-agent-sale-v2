# Developer memory — agentic-rag-retry-loop

## 2026-07-15 — agentic-rag-retry-loop: reusable lessons

- **A non-standard project layout needs `sdlc.config.json paths.code_roots` set explicitly, and
  even then root-level files aren't covered.** The built-in developer write-fence globs
  (`src/**`, `app/**`, `lib/**`, …) assume a conventional layout; a FastAPI project with
  `api/`, `core/`, `services/`, `models/`, `cli/` at the repo root needed `paths.code_roots` set
  to those globs before S4 could write ANY source file. Directory globs still don't cover
  root-level filenames like `.env.example` — if a task needs to edit a root config/env file,
  either add the exact filename to `paths.code_roots` or expect the edit to be blocked (D5 in this
  change). Check this at S3→S4 handoff for any project whose source doesn't live under `src/`.
- **`pytest-cov` with a dotted `--cov=package.module` argument can crash a numpy/C-extension-heavy
  test suite** (`ImportError: cannot load module more than once per process`) — a coverage.py
  import-tracing interaction with numpy's one-time-load guard, not a code bug. Directory-style
  `--cov=some/dir` args work; a single-file path (`--cov=some/dir/file.py`) silently collects zero
  data instead of erroring. When you need coverage for one specific file in a numpy-adjacent repo,
  point `--cov` at its parent directory (or the whole package) and read that one file's row out of
  `--cov-report=term-missing` rather than passing the file/module directly.
- **A retry loop that reuses the same DB session across attempts can turn a benign, gracefully-
  handled first-attempt error into a hard failure on the second attempt.** Here, a pre-existing
  FTS→vector-only fallback caught a `ProgrammingError` on attempt 0 without rolling back — harmless
  when there was only ever one attempt per request, but the new attempt-1 re-query on the same
  (now-aborted) transaction raised outright. The abort-to-best-seen guard caught it cleanly, but the
  "recovery" test still failed because attempt 1 never got a clean session. General pattern: adding
  a retry loop that shares infrastructure (DB session, HTTP connection) across attempts can surface
  latent "works once, poisons state on error" bugs in code that was never exercised twice per
  request before. Worth a design-time question: does each retry attempt need a fresh
  session/transaction, or is reuse safe only because the existing code never errors mid-transaction?

## 2026-07-16 — agentic-rag-retry-loop: S6 release lessons

- **`openspec archive` is interactive and will hang a non-interactive shell** if `tasks.md` has any
  `- [ ]` left (even one already-done-in-reality-but-not-checked item) or when it asks "Proceed with
  spec updates?" — pipe `yes |` into the command, but first reconcile `tasks.md` checkboxes against
  what actually happened (here, task 1.2 was resolved during the S4-fix pass but the checkbox was
  never flipped, so archive saw "16/17" and prompted). Fix the checkbox truth *before* archiving,
  don't just force through the prompt.
- **After `openspec archive`, the change directory moves** to
  `openspec/changes/archive/{ISO-date}-{change-name}/` (date-prefixed). Any CPP baton write after
  archiving (`_decisions.jsonl`, `_progress.md`, `state-set.mjs`) must target that new path.
  `state-set.mjs --change <name>` only joins one path segment under `openspec/changes/`, but it does
  accept a slash-containing value — `--change "archive/{ISO-date}-{change-name}"` resolves correctly
  without needing the tool's source changed.
