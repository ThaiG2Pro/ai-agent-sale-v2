# Implementation Plan: Human-in-the-Loop (HITL) Control System

**Branch**: `004-human-in-loop-hitl` | **Date**: 2026-03-06 | **Spec**: [spec.md](./spec.md)  
**Input**: Feature specification from `/specs/004-human-in-loop-hitl/spec.md`

---

## Summary

Embed a **Human-in-the-Loop (HITL) control layer** directly inside the existing LangGraph sales agent to intercept sensitive, revenue-affecting operations before they execute. The system uses **`interrupt()`** (dynamic, node-level breakpoints) combined with `AsyncPostgresSaver` (single source of truth for state) and an idempotent `/review` API endpoint with optimistic locking. Three escalation paths are handled: manual order approval, confidence guards (< 0.7), and cost guards (> 8000 tokens). Customer messages queued during pause are consumed first on resume via a post-approval node. Rejections route through a `customer_support_node` for empathetic messaging instead of abrupt termination.

**Technical approach (from `docs/week4/techniques-reference.md`)**:
- **Technique 1** (HITL & Transaction Safety): `interrupt()` + `AsyncPostgresSaver` + `Command(resume=value)`
- **Technique 2** (Transparent State Inspection): `graph.aget_state(config)` + `StateSnapshot`
- **Technique 3** (Idempotent Review Gateway): `Idempotency-Key` header + `update_state(config, values)` + optimistic locking
- **Technique 4** (State Override): `graph.update_state(..., as_node="hitl_review_node")` + Pattern B (atomic update + resume)
- **Technique 5** (Confidence Guard): RRF fusion formula + threshold 0.7
- **Technique 6** (Cost Guard): `litellm.token_counter()` + circuit-breaker interrupt
- **Technique 8** (Confidence Scoring): Fusion formula `(1-α)·similarity + α·rerank`, α=0.7
- **Technique 9** (Tool Contract Testing): `pytest-asyncio` + `respx.mock` + `SecretStr`

---

## Technical Context

**Language/Version**: Python 3.13+  
**Primary Dependencies**: LangGraph ≥ 0.3, FastAPI (async), SQLAlchemy 2.0 (async), asyncpg, LiteLLM, Pydantic v2, langgraph-checkpoint-postgres  
**Storage**: PostgreSQL 17 + pgvector 0.8 (schema: `agent_v1`). LangGraph checkpointer tables: `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`. New HITL tables: `hitl_metadata`, `review_actions`, `confidence_scores`, `queued_messages`, `support_queue`.  
**Testing**: pytest + pytest-asyncio; respx for HTTP mocks; deterministic TDD for API/DB layer; Gold Dataset eval for agent workflows  
**Target Platform**: Linux server (Docker Compose, python:3.13-slim-bookworm)  
**Project Type**: Single web project (existing monorepo: `api/`, `core/`, `services/`, `models/`)  
**Performance Goals**: `/review` endpoint < 200ms p95; state inspection < 10ms (connection pool); auto-reply queuing < 2s  
**Constraints**: No Redis (lean SME); PostgreSQL only; async-only I/O; max 2 HITL pauses per order; 90-day message retention  
**Scale/Scope**: SME-scale (hundreds of concurrent sessions); single admin or small team approval workflow

---

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Article | Requirement | Status | Notes |
|---------|-------------|--------|-------|
| Art. I (Modular Core) | HITL logic in `core/agent/nodes/` and `services/hitl.py`; API layer in `api/routes/hitl.py` | ✅ PASS | No business logic in route handlers |
| Art. II (Anti-Abstraction) | Use LangGraph `interrupt()` + `update_state()` directly; no custom HITL engine | ✅ PASS | Framework features used natively (exemption for LangGraph orchestration) |
| Art. III (TDD) | Deterministic: API endpoints, DB writes, optimistic locking → TDD. Non-deterministic: confidence guard routing, post-approval node responses → Gold Dataset eval | ✅ PASS | Follows lean tiered evaluation |
| Art. V (Async) | All DB ops via `AsyncSession`; checkpointer via `AsyncPostgresSaver`; no blocking reranker in event loop | ✅ PASS | Runs in existing async FastAPI server |
| Art. VI (Type Safety) | `AgentState` extended with HITL fields (TypedDict); all API boundaries via Pydantic v2 models | ✅ PASS | Strict Pydantic validation on `/review` |
| Art. VIII (Critical Actions) | Order placement, refund, pricing → guarded by `interrupt()` before execution | ✅ PASS | Core requirement of this feature |
| Art. XI (Docs) | Docstrings with "Why this exists" in all new nodes, services, models | ✅ PASS | Must add to all new files |
| Art. XII (Cost Efficiency) | Cost guard implemented; test cases assert cheap model used for simple queries | ✅ PASS | `litellm.token_counter()` circuit breaker |

**Constitution Check Result: ✅ ALL GATES PASS** — proceed to Phase 0 research.

---

## Project Structure

### Documentation (this feature)

```text
specs/004-human-in-loop-hitl/
├── plan.md              ← This file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
├── contracts/
│   └── hitl-api.yaml    ← Phase 1 output (OpenAPI)
└── tasks.md             ← Phase 2 output (/speckit.tasks — NOT created here)
```

### Source Code (repository root)

```text
core/
└── agent/
    ├── state.py                        ← Extend AgentState with HITL fields
    └── nodes/
        ├── hitl_guard.py               ← NEW: confidence + cost guard, triggers interrupt()
        ├── post_approval.py            ← NEW: consume QueuedMessages, inject synthetic messages
        └── customer_support.py         ← NEW: empathetic rejection response → __end__

services/
└── hitl.py                             ← NEW: HITLService (pause, resume, review, timeout)

api/
└── routes/
    └── hitl.py                         ← NEW: /review, /session/{id}/state endpoints

models/
└── schema.py                           ← Extend: add HITLMetadata, ReviewAction, QueuedMessage, SupportQueue

migrations/
└── versions/
    └── XXXX_add_hitl_tables.py         ← NEW: Alembic migration

tests/
├── unit/
│   ├── test_hitl_guard.py              ← NEW: confidence/cost threshold logic
│   ├── test_post_approval_node.py      ← NEW: queue consumption, synthetic message placement
│   └── test_hitl_service.py            ← NEW: optimistic locking, timeout logic
├── contract/
│   └── test_hitl_api.py               ← NEW: /review endpoint contract tests
└── integration/
    └── test_hitl_flow.py              ← NEW: end-to-end pause → approve → resume flow
```

**Structure Decision**: Extends existing single-project monorepo. HITL logic in `core/` (business logic), `services/` (DB operations), `api/routes/` (HTTP boundary). Follows existing pattern established in Weeks 2–3.

---

## Complexity Tracking

> No constitution violations. No extra projects, no repository patterns. LangGraph orchestration already exempted by Article II.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| None | — | — |
