# Implementation Plan: Human-in-the-Loop (HITL) Control System

**Branch**: `004-human-in-loop-hitl` | **Spec**: [`specs/004-human-in-loop-hitl/spec.md`](./spec.md)  
**Input**: Feature specification v5 (FR-001–033, SC-001–035, 10 edge case fixes)

---

## Summary

Extend the Week 3 LangGraph sales agent with a **5-layer HITL control system** that pauses the
graph at `hitl_guard_node` using LangGraph's `interrupt()` mechanism whenever an order
requires admin approval, confidence is too low, or cost exceeds threshold.  Admin reviews the
pause via a REST `/review` endpoint, optionally applying state edits, then issues
`Command(resume=payload)` to continue the graph through `queue_consumer_node`.

`queue_consumer_node` is the central integration point: it processes the customer message queue
accumulated during the pause, classifies intent in batch (cheap model), and routes to
`state_freshness_validator_node` (CONFIRM), `cancellation_node` (CANCEL), or re-pauses for
MODIFY_ORDER.  A background `asyncio` task handles 30-min notification and 60-min
SupportQueue escalation.  All 10 identified edge cases are handled via dedicated guard logic
(see `research.md` → Decisions 3–9 and `spec.md` §Edge Cases).

**Key architectural decision**: `interrupt()` inside `hitl_guard_node` (dynamic breakpoint),
NOT `interrupt_before` (static).  Reason: resumes AFTER the `interrupt()` call, enabling
`Command(goto="queue_consumer_node")` routing, whereas `interrupt_before` would skip
`queue_consumer_node` entirely.

---

## Technical Context

**Language/Version**: Python 3.13+  
**Primary Dependencies**: LangGraph ≥ 0.3, `langgraph-checkpoint-postgres` (AsyncPostgresSaver),
`psycopg[binary]` ≥ 3.1.9 (psycopg3 — separate from existing `asyncpg`),
FastAPI (async), LiteLLM (economy model for queue classification), SQLAlchemy 2.0 async  
**Storage**: PostgreSQL 17 + pgvector 0.8+ (schema `agent_v1`); 5 HITL application tables
+ 4 LangGraph checkpointer tables (auto-created by `AsyncPostgresSaver.setup()`)  
**Testing**: pytest + pytest-asyncio; existing 130 tests as baseline  
**Target Platform**: Linux server, Docker Compose  
**Performance Goals**: `/review` endpoint p95 < 200 ms; `queue_consumer_node` batch
classification < 500 ms (economy model, ≤ 5 queued messages)  
**Constraints**: No Redis, no Celery, no blocking event-loop calls; single PostgreSQL database;
`JsonPlusSerializer(pickle_fallback=False)` mandatory (CVE-2026-27794)  
**Scale/Scope**: SME — single admin reviewer, < 100 concurrent paused sessions

---

## Constitution Check

*All 12 articles checked. No violations.*

| Article | Rule | Status |
|---------|------|--------|
| I — Single Source of Truth | PostgreSQL only; no Redis, no in-memory state | ✅ `AsyncPostgresSaver` → PostgreSQL |
| II — LangGraph Mandatory | Orchestration via LangGraph StateGraph | ✅ HITL nodes integrated into existing graph |
| III — No Over-Engineering | No K8s, no Celery, no microservices | ✅ `asyncio` task for timeout, Docker Compose only |
| IV — Local-First / Zero-Cost | Must work fully offline with Ollama | ✅ Queue classification uses economy/local model |
| V — Strict Async | No blocking I/O in event loop | ✅ `asyncpg` + `psycopg[async]`; timeout loop is async; `anyio.to_thread` if needed |
| VI — Pydantic Boundaries | All LLM outputs and API payloads via Pydantic models | ✅ `ReviewAction`, `ApprovalPayload`, queue intent output |
| VII — Stateless Runtime | Runtime reads state from PostgresSaver; no in-process state | ✅ `AsyncPostgresSaver` is sole state store |
| VIII — Human Circuit Breaker | Irreversible actions must have HITL gate | ✅ This feature IS the Article VIII implementation |
| IX — RAG Grounding | Citations required for info responses | ✅ Unchanged from Week 3 |
| X — Economy Model Default | Use cheap model unless escalation warranted | ✅ Queue classification uses economy tier |
| XI — Observability | Structured logging + OpenTelemetry traces | ✅ HITL events traced; `SensitiveLogFilter` on admin endpoints |
| XII — No Hardcoded Secrets | All secrets via env vars | ✅ `ADMIN_API_KEY`, `DATABASE_URL` from `.env` |

---

## Project Structure

### Documentation (this feature)

```text
specs/004-human-in-loop-hitl/
├── plan.md              ← This file
├── spec.md              ← Authoritative spec (FR-001–033, SC-001–035)
├── research.md          ← 9 implementation decisions resolved
├── data-model.md        ← DB schema + AgentState extension + state transitions
├── quickstart.md        ← Dev setup commands
├── contracts/
│   └── hitl-api.yaml    ← OpenAPI spec for /state and /review endpoints
└── tasks.md             ← 73 granular tasks across 21 phases
```

### Source Code (additions to existing single-project layout)

```text
# New graph nodes
core/agent/nodes/
├── hitl_guard.py            # confidence + cost guard; calls interrupt() on threshold breach
├── queue_consumer.py        # orphan tool scan + batch intent classify + routing
├── state_freshness.py       # re-query inventory/price freshness (stale data guard)
├── order_execution.py       # actual order placement (post-freshness-check)
├── cancellation.py          # customer CANCEL path
└── customer_support.py      # rejection / escalation messaging

# State extension
core/agent/
├── state.py                 # ADD: HITLReasonEnum, hitl_* fields, order_info
└── graph.py                 # ADD: AsyncPostgresSaver setup, 6 new nodes, updated edges

# Checkpointer factory
core/agent/
└── checkpointer.py          # AsyncPostgresSaver + AsyncConnectionPool + JsonPlusSerializer

# HITL service layer
services/hitl/
├── __init__.py
├── service.py               # HITLService: gateway check, resume, timeout scheduler
├── schemas.py               # Pydantic: ReviewAction, ApprovalPayload, QueuedMessageBatch
└── timeout_scheduler.py     # asyncio background task: 30-min warn, 60-min escalate

# Admin API route
api/routes/
└── hitl.py                  # GET /hitl/session/{session_id}/state
                             # POST /hitl/review
                             # X-Admin-Key + X-Idempotency-Key guards

# DB models
models/schema.py             # ADD: HITLMetadata, ReviewAction, QueuedMessage, SupportQueue,
                             #      InterruptedSession ORM models

# Alembic migration
migrations/versions/
└── XXXX_add_hitl_tables.py  # Creates 5 agent_v1 tables (HITLMetadata, ReviewAction,
                             #   QueuedMessage, SupportQueue, InterruptedSession)
                             # AsyncPostgresSaver creates its own 4 tables via setup()

# Tests (new)
tests/unit/
├── test_hitl_guard_node.py       # interrupt() fires, resume routing, overflow guard
├── test_queue_consumer_node.py     # orphan tool close, CANCEL override, MODIFY_ORDER re-pause
├── test_state_freshness_node.py    # stale price detection, out-of-stock routing
├── test_hitl_service.py            # gateway, idempotency, optimistic lock, no-auto-resume
└── test_hitl_schemas.py            # Pydantic validation for ReviewAction payload

tests/integration/
└── test_hitl_flow.py               # end-to-end: pause → approve → queue_consumer → execute
                                    #             pause → reject → customer_support
                                    #             CANCEL during pause overrides admin approval

tests/contract/
└── test_hitl_api.py                # FastAPI TestClient: /state, /review, idempotency header, 409

# Existing files modified
core/agent/state.py          # extend AgentState TypedDict with 8 HITL fields
core/agent/graph.py          # add AsyncPostgresSaver, 6 nodes, new edges
api/main.py                  # mount hitl router; add AsyncPostgresSaver lifespan startup
models/schema.py             # add 5 ORM models
core/config.py               # add 7 HITL settings + DATABASE_URL_PSYCOPG computed field
```

---

## Complexity Tracking

> No constitution violations. No exceptions required.

| Layer | Simplest valid approach | Selected approach | Difference |
|-------|------------------------|-------------------|------------|
| Timeout scheduler | APScheduler (3rd party) | `asyncio.create_task` loop in FastAPI lifespan | No new dependency |
| Idempotency store | Redis | `review_idempotency` column in `ReviewAction` table | Postgres only |
| Checkpointer | Custom JSON table | `AsyncPostgresSaver` (LangGraph built-in, psycopg3) | Standard library |
| State override | Direct JSON patch | `graph.update_state(as_node=...)` + `Command(resume)` | Correct audit trail |
| Re-pause for MODIFY | Always re-pause | Compare queued modification vs current `order_info`; only re-pause if different | Prevents Double Correction (Edge Case 1) |
