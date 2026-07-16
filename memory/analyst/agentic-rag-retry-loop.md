# Analyst memory — agentic-rag-retry-loop

## 2026-07-15 — agentic-rag-retry-loop: deltaMode=MODIFIED fails when openspec/specs/ is empty

**Lesson (reusable):** When a `cr` modifies behavior that lives only in CODE (not yet in any living
OpenSpec spec) and `openspec/specs/` is empty, `deltaMode=MODIFIED` cannot validate — OpenSpec's
MODIFIED requires a matching `### Requirement:` header in the base living spec. Author the delta under
`## ADDED Requirements` to establish the capability spec for the first time, write the requirements to
describe the NEW target behavior, and explicitly flag the ADDED-vs-MODIFIED discrepancy in the
proposal + handoff for the gate owner. `openspec validate <change> --strict` then passes.

## 2026-07-15 — agentic-rag-retry-loop: reuse existing scoring signals, don't invent a parallel scorer

**Lesson (reusable):** When adding an "agentic" self-evaluation/retry loop to an existing pipeline,
the cheapest and most defensible design reuses the signals the codebase already computes (here
`best_similarity` / `chunks_after` / `confidence_score` from `confidence_node`) as the numeric gate,
and confines any new LLM call to the action (query rewrite), not to re-scoring. Watch for a watch-item
that says "reuse X, not a new Y" — turn it into an explicit AC + BR so the architect can't drift into
a second scorer.

## 2026-07-15 — agentic-rag-retry-loop: a bounded LLM loop is a DoS/cost + prompt-injection surface

**Lesson (reusable):** Any "retry until confident / loop with LLM" feature is a runaway-cost/DoS risk
and a prompt-injection surface (user text flows into a rewrite/grade prompt). Always spec: a hard
configurable cap (with 0 = kill switch), a no-progress early stop, a per-attempt bound, and a rule
that LLM-authored output is used ONLY as data (a retrieval query), never executed. Fold these into
Early Risk Flags as STRIDE DoS + Tampering/Info-Disclosure even when `stride_analysis=auto` and the
feature doesn't touch the classic auth/payment/PII triggers.
