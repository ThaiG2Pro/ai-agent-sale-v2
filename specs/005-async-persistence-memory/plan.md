# Implementation Plan: Async Persistence & Memory

**Branch**: `005-async-persistence-memory` | **Date**: 2026-03-11 | **Spec**: [spec.md](./spec.md)  
**Depends on**: `004-human-in-loop-hitl` — AsyncPostgresSaver, HITLMetadata, ReviewAction tables, and HITL graph nodes are prerequisites.

## Summary

Give the AI Sales Agent persistent long-term memory. Week 4 already provides durable graph checkpoints via `AsyncPostgresSaver` — Week 5 builds four layers on top: **structured conversation summarization** (compress 20+ message threads before sending to LLM), **semantic memory** (vector-embed summaries for cross-session retrieval), **sales intent extraction** (structured Budget/Urgency/Product signals gated on signal-bearing turns only), and **intent tracking** (per-customer CRM record with optimistic locking). All four post-turn operations are dispatched as parallel async background tasks so the customer-facing TTFT is unchanged.

**Integration surface for Week 6**: `customer_id` maps to Telegram `user_id` (string). Week 6 merely needs to populate `AgentState.customer_id` from the webhook payload — all memory services are already keyed by it.

---

## Technical Context

**Language/Version**: Python 3.13+  
**Primary Dependencies**: FastAPI (async), LangGraph + AsyncPostgresSaver (already wired in W4), SQLAlchemy 2.0 async + asyncpg, LiteLLM (LIGHT_CHAT_MODEL for summarization), pgvector 0.8+ (HNSW already in use), Pydantic v2  
**Storage**: PostgreSQL 17 — `agent_v1` schema (single-DB, no Redis)  
**Testing**: pytest-asyncio + real Postgres (Article IV integration-first)  
**Target Platform**: Linux Docker container (python:3.13-slim-bookworm)  
**Performance Goals**: Semantic memory retrieval < 500ms p95 (SC-005); TTFT increase ≤ 50ms over baseline (SC-009)  
**Constraints**: Connection pool ≤ 20 connections (FR-002); zero ORM lazy loading; no blocking I/O in event loop; all 4 post-turn tasks parallelised; optimistic lock retries ≤ 3  
**Scale/Scope**: SME scale — 15–20 concurrent conversations; 500+ stored summaries; single Postgres instance

---

## Constitution Check

*GATE: Must pass before Phase 0 research.*

| Article | Requirement | Compliance |
|---------|-------------|------------|
| I — Modularity | Business logic in `services/memory/`, not in API handlers | ✅ Memory services in `services/memory/`; API in `api/routes/memory.py` |
| II — Simplicity | Max 3 projects; no unnecessary abstraction | ✅ Single project; no repository pattern; direct SQLAlchemy Core for updates |
| III — TDD | Deterministic code via TDD; AI components via evaluation | ✅ Summarizer/intent logic = evaluation-first; DB queries = TDD |
| IV — Integration-first | Real DB in tests; contract tests before impl | ✅ `tests/contract/test_memory_api.py` before route impl |
| V — Async | All I/O async; no blocking in event loop; local reranker off-loop | ✅ All services async; background tasks via `asyncio.create_task` |
| VI — Structured outputs | Pydantic models for all LLM outputs; no regex | ✅ `SalesIntentExtraction` Pydantic model; `ConversationSummaryOutput` model |
| VII — Stateless runtime | All state in Postgres; checkpoint after every transition | ✅ 4 new tables; AsyncPostgresSaver handles graph state |
| VIII — HITL | Right-to-be-forgotten deletion requires explicit admin confirmation for pending HITL | ✅ FR-019; deletion blocked on `HITLMetadata.status == 'paused'` |
| IX — Citation / no hallucination | Intent fields stored as null/UNKNOWN when not found | ✅ FR-013; Pydantic model defaults unknown fields to null |
| X — Cost | Summarization uses LIGHT_CHAT_MODEL; intent extraction gated on signal-bearing turns | ✅ FR-011 skip logic; FR-010.2 model selection |
| XI — Docs as code | ADR for HNSW params + embedding governance | ✅ ADR-005 in docs/adr/ |
| XII — Efficiency metric | Tests assert cheap model used for summarization; escalation not triggered | ✅ Tier 1 eval: assert `model_used == LIGHT_CHAT_MODEL` in summary records |

**No violations. No Complexity Tracking entries required.**

---

## Project Structure

### Documentation (this feature)

```text
specs/005-async-persistence-memory/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
├── contracts/
│   └── memory-api.yaml  ← Phase 1 output
└── tasks.md             ← Phase 2 output (/speckit.tasks)
```

### Source Code (additions only — existing structure preserved)

```text
# NEW: Memory service layer (Week 5 core)
services/memory/
├── __init__.py
├── background.py        # post_turn_tasks(): parallel coordinator (FR-003b)
├── summarizer.py        # ConversationSummarizer: trigger at 20 msgs, LiteLLM call
├── semantic_memory.py   # SemanticMemoryService: store/retrieve/flag_stale (FR-007–010b)
├── intent_extractor.py  # SalesIntentExtractor: gated extraction (FR-011–013)
└── intent_tracker.py    # IntentTracker: upsert with optimistic lock (FR-015b)

# NEW: Memory admin API route
api/routes/
└── memory.py            # GET /memory/intent/{customer_id}
                         # GET /memory/intent?urgency=&status=
                         # GET /memory/semantic/{customer_id}
                         # DELETE /memory/customer/{customer_id}

# NEW: Graph node for memory retrieval
core/agent/nodes/
└── memory_retrieval.py  # memory_retrieval_node: runs before answer_node (FR-008)

# MODIFIED: Extend existing files
core/agent/state.py      # ADD: customer_id, memory_context, memory_retrieval_scores,
                         #      thread_summary_exists, sales_intent_skipped
core/agent/graph.py      # ADD: memory_retrieval_node before answer_node;
                         #      asyncio.create_task(post_turn_tasks()) after ainvoke
models/schema.py         # ADD: ConversationSummary, SemanticMemory,
                         #      SalesIntentLog, IntentTracking ORM models
core/agent/state.py      # Extend IntentEnum: FOLLOW_UP, OTHER
migrations/versions/
└── XXXX_add_memory_tables.py  # 4 new agent_v1 tables + HNSW index on semantic_memory

# MODIFIED: Config additions
core/config.py           # ADD: MEMORY_SUMMARY_THRESHOLD (default 20)
                         #      MEMORY_RELEVANCE_THRESHOLD (default 0.75)
                         #      MEMORY_TOP_K (default 3)
                         #      CHECKPOINT_SIZE_WARN_BYTES (default 1_048_576)
                         #      CHECKPOINT_RETENTION_DAYS (default 90)

# NEW: Tests
tests/unit/
├── test_summarizer.py
├── test_semantic_memory.py
├── test_intent_extractor.py
└── test_intent_tracker.py      # optimistic lock race condition test

tests/integration/
└── test_memory_flow.py          # full: send 21 msgs → summary created → restart → memory recalled

tests/contract/
└── test_memory_api.py           # GET/DELETE endpoints before implementation

docs/adr/
└── ADR-005-memory-hnsw-embedding-governance.md
```

**Structure Decision**: Single project, extending existing structure. `services/memory/` follows the same pattern as `services/hitl/` from Week 4. No new projects, no new top-level directories.

---

## Complexity Tracking

> No constitution violations. No exceptions required.

| Layer | Simplest valid approach | Selected approach | Difference |
|-------|------------------------|-------------------|------------|
| Optimistic lock | DB-level lock / SELECT FOR UPDATE | `UPDATE ... WHERE version = :v` + `rowcount` check, max 3 retries | No deadlock; async-safe; no row-level locks held |
| Background tasks | FastAPI BackgroundTasks (runs after response) | `asyncio.create_task` dispatched inside `ainvoke` wrapper | Truly fire-and-forget; decoupled from HTTP response cycle |
| Summarization trigger | Cron job | Reactive: message count check after every turn | No scheduler dependency; consistent with event-driven arch |
| Embedding model versioning | Manual convention | `model_version = f"{model_name}@{dimension}"` stored per row; STALE flag on mismatch | Zero additional infrastructure; detectable at search time |
| Right-to-be-forgotten | Manual SQL delete | Transactional cascade delete per `customer_id` + HITL confirmation gate | Single operation; auditable; safe with pending HITL |
