# Implementation Plan: Agentic Workflow & Safe Logic

**Branch**: `003-agentic-workflow` | **Date**: 2026-03-03 | **Spec**: [spec.md](spec.md)  
**Input**: Feature specification from `/specs/003-agentic-workflow/spec.md`

## Summary

Transform the existing Week 2 RAG pipeline (`services/rag/pipeline.py` → `answer_with_rag()`) into a fully controllable LangGraph state machine. The agent receives a user message, classifies intent via the economy model (Pydantic output), routes to the correct processing path, invokes the RAG pipeline as a typed async tool, applies intent-first model escalation (COMPLAINT/NEGOTIATION → premium unconditionally), fuses similarity + rerank confidence scores, and returns a fully populated `AgentState` with citations and a model trace. All tool I/O is validated through Pydantic schemas. Contract tests are written before implementation. Per-node streaming is exposed for development debugging.

The Week 2 RAG pipeline remains intact — the agent wraps it as a tool, not a replacement.

---

## Technical Context

**Language/Version**: Python 3.13+  
**Primary Dependencies**: LangGraph 0.3+, LiteLLM (Router, latest), FastAPI (Async), SQLAlchemy 2.0 (asyncpg), Pydantic v2, logfire, pytest-asyncio, respx  
**Storage**: PostgreSQL 17 + pgvector 0.8+, schema `agent_v1` (existing). `model_traces` table already exists. No new tables required for Week 3.  
**Testing**: pytest + pytest-asyncio (async), respx (HTTP mock at transport layer — contract tests), heuristic assertions (Tier 1 CI per Article III)  
**Target Platform**: Linux / Docker Compose (same Compose stack as Week 1/2)  
**Project Type**: Single project (existing layout: `core/`, `services/`, `api/`, `models/`, `cli/`, `tests/`)  
**Performance Goals**: Intent classification node < 300ms (economy model). Full agent turn P95 < 3s (offline/Ollama). Low-confidence fallback (no LLM) < 200ms.  
**Constraints**: Zero cost in dev (Ollama only). No blocking I/O in event loop (Article V). No global mutable state (Article VII). Agent recursion limit: 5 turns (Article X). No direct LLM SDK imports — LiteLLM Router only (constitution). **Ollama G1 constraint**: All Ollama calls across nodes must use the same model alias where possible; router node uses `economy-chat` (not `light-chat`) to prevent VRAM thrashing between router → retrieval → answer (Week 2 documented gotcha).  
**Scale/Scope**: Single-user dev sessions (Week 3). Multi-user readiness deferred to Week 6 (Telegram webhook). Agent graph has **5 nodes** (router_node, retrieval_node, escalation_node, confidence_node, answer_node) connected by edges defined in `build_graph()`.

---

## Constitution Check

*GATE: Must pass before implementation begins. Re-checked after Phase 1 design.*

| Article | Requirement | Status | Notes |
|---------|-------------|--------|-------|
| I — Modularity | Business logic in `core/agent/`, callable without API | ✅ PASS | `core/agent/graph.py` compilable standalone; `run_agent.py` CLI permitted |
| I — CLI | RAG CLI already exists; agent gets `run_agent.py` debug script only | ✅ PASS | No separate CLI parser needed per constitution exemption |
| II — Simplicity | No Repository pattern; no extra abstraction layers | ✅ PASS | LangGraph is the one permitted abstraction (constitution exemption) |
| III — TDD | Deterministic nodes (router, confidence guard) → strict TDD. Agent flow → Tier 1 heuristic eval | ✅ PASS | Contract tests before tool implementation (FR-009) |
| IV — Integration-First | Contract tests mandatory before implementation; prefer real DB in integration tests | ✅ PASS | Contract tests in `tests/contract/tools/`; integration tests reuse existing conftest |
| V — Async | All graph nodes async. Local CrossEncoder (if used) via `anyio.to_thread.run_sync` | ✅ PASS | No `requests`, no `psycopg2`, no blocking calls |
| VI — Structured Output | Intent classification returns Pydantic model. No regex on LLM output. Enums for intent/escalation. | ✅ PASS | `IntentClassification`, `EscalationDecision` Pydantic models |
| VII — Stateless Runtime | `MemorySaver` for dev, `AsyncPostgresSaver` for prod. No in-memory conversation state. | ✅ PASS | State persisted in DB after every graph step. `AsyncPostgresSaver` auto-initializes its own tables (`checkpoints`, `checkpoint_writes`, `checkpoint_migrations`) on first use — no manual Alembic migration needed for Week 3. Alembic migration scope for `agent_v1` schema is limited to application tables; LangGraph checkpointer tables are library-managed. Alembic migration file for Week 5 persistent memory (when `AsyncPostgresSaver` is activated) is out of scope for Week 3. |
| VIII — Human Circuit Breaker | HITL (`interrupt_before`) deferred to Week 4. Week 3 adds the escalation node that Week 4 will interrupt. | ✅ PASS | No Critical Actions executed in Week 3 |
| IX — Citation | Citations already produced by RAG tool. Agent state propagates `citations` field via TypedDict reducer. Each citation has `source_text` for grounding (T014 Citation model). | ✅ PASS | `citations` in AgentState, no separate `source_chunk_ids` field needed |
| X — Token Economy | Economy model for intent. Premium only on escalation. Recursion limit = 5. Token cost logged in `model_traces`. | ✅ PASS | `LITELLM_CONFIG` already defines `light-chat`, `economy-chat`, `premium-local-chat` |
| XI — Docs as Code | Docstrings with "Why this exists / What it does". ADR for LangGraph choice. | ✅ PASS | ADR-002 planned (LangGraph vs manual loop) |
| XII — Efficiency Metric | Tier 1 CI tests assert `model_used == economy-chat` for INFO_QUERY. Assert escalation for COMPLAINT. | ✅ PASS | Part of contract test DoD |

**Gate result: ALL PASS — implementation may proceed.**

---

## Project Structure

### Documentation (this feature)

```text
specs/003-agentic-workflow/
├── plan.md              ← this file
├── research.md          ← Phase 0 output
├── data-model.md        ← Phase 1 output
├── quickstart.md        ← Phase 1 output
├── contracts/
│   ├── rag_tool.md      ← RAGTool input/output contract
│   └── inventory_tool.md ← InventoryTool stub contract
└── tasks.md             ← Phase 2 output (/speckit.tasks)
```

### Source Code (new files for Week 3)

```text
core/
└── agent/
    ├── __init__.py
    ├── state.py          # AgentState TypedDict + reducers + Pydantic boundary models
    ├── tools.py          # @tool async functions (rag_search, inventory_lookup stubs)
    ├── nodes/
    │   ├── __init__.py
    │   ├── router.py     # Intent classification node → IntentClassification Pydantic output
    │   ├── escalation.py # EscalationDecision node — intent-first + score-based
    │   ├── retrieval.py  # Invokes RAGTool, populates state.citations + scores
    │   ├── confidence.py # Fuses similarity + rerank → confidence_score; guardrail
    │   └── answer.py     # Generates final response via LiteLLM (selected model)
    └── graph.py          # StateGraph compile(), exports Mermaid, exposes stream()

cli/
└── run_agent.py          # Debug CLI (Article I exemption) — offline LangGraph runner

tests/
├── contract/
│   └── tools/
│       ├── __init__.py
│       ├── test_rag_tool_contract.py       # respx + Pydantic schema validation
│       └── test_inventory_tool_contract.py # stub contract (404/429/500/timeout)
├── unit/
│   ├── test_agent_state.py     # TypedDict serialization, reducer correctness
│   ├── test_router_node.py     # Intent classification determinism (mocked LLM)
│   ├── test_confidence_node.py # Fusion formula, threshold guard
│   └── test_escalation_node.py # Intent-first logic correctness
└── integration/
    └── test_agent_flow.py      # Full graph run (MemorySaver, mocked LLM or Ollama)
```

**Structure Decision**: Single project, extending existing layout. Agent logic lives in `core/agent/` (Article I: core business logic). CLI debug script in `cli/` (Article I: agent CLI exemption). No new top-level directories. Test layout matches Article IV hierarchy: contract → integration → unit.

---

## Complexity Tracking

> No constitution violations. LangGraph exemption already documented in Article II.

| Item | Why Permitted |
|------|--------------|
| LangGraph StateGraph | Article II explicit exemption: "LangGraph is explicitly permitted and mandated as the orchestration layer" |
| `anyio.to_thread.run_sync` for CrossEncoder | Article V.2 + tech reference blocking policy: CPU-bound ML off event loop in dev |
