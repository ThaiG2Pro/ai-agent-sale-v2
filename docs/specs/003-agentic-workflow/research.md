# Research: Agentic Workflow & Safe Logic (Week 3)

**Phase 0 — All NEEDS CLARIFICATION resolved**  
**Date**: 2026-03-03  
**Sources**: `docs/week3/techniques-reference.md`, existing codebase analysis

---

## Decision 1: LangGraph State Type

**Decision**: `TypedDict` for `AgentState` with Pydantic models at I/O boundaries only.

**Rationale**: Tech reference §1 confirms TypedDict as the correct choice for internal agent state — serializable, zero overhead, direct JSONB storage in PostgreSQL. Pydantic is used at boundaries (LLM output, tool I/O, API response) per Article VI. Mixing Pydantic into the core state adds validation overhead on every reducer call with no benefit.

**Alternatives considered**:
- Pydantic BaseModel for state: rejected — ORM-style overhead, breaks LangGraph reducer pattern
- Dataclass: rejected — no default reducer support, manual serialization required

**Reducers**:
- `messages`: `add_messages` (deduplication by ID, critical for retries — tech ref §1)
- `citations`: `operator.add` (accumulate across nodes)
- All scalar fields: default replace/overwrite

---

## Decision 2: Routing & Command API

**Decision**: Use LangGraph `Command(goto=..., update={...})` for dynamic routing from the router node, not static `add_conditional_edges`.

**Rationale**: Tech reference §5 recommends Command API (2026 pattern) for runtime routing decisions based on intent classification. This allows the router to return both a routing decision AND a state update (the classified intent) in a single node output — no separate node needed.

**Alternatives considered**:
- `add_conditional_edges` with a routing function: rejected — separates routing logic from state update, harder to trace
- Hardcoded edges: rejected — violates ADAPTIVE > STATIC principle

---

## Decision 3: Intent Categories

**Decision**: Five intents: `INFO_QUERY`, `PRICING`, `COMPLAINT`, `NEGOTIATION`, `SMALLTALK`.

**Rationale**: Week 2 RAG pipeline (`services/ai.py` → `NormalizedQuery`) already classifies `INFO_QUERY | PRICING | COMPARISON | COMPLAINT | NEGOTIATION | AVAILABILITY | OTHER`. The agent router extends this with `SMALLTALK` (chitchat bypass) and consolidates `AVAILABILITY`/`COMPARISON`/`OTHER` → `INFO_QUERY` to keep routing logic to 3 branches: escalate | rag | bypass.

**Mapping**:
- `COMPLAINT`, `NEGOTIATION` → escalate_node (premium model, no RAG first)
- `INFO_QUERY`, `PRICING`, `COMPARISON`, `AVAILABILITY` → retrieval_node → confidence_node → answer_node
- `SMALLTALK` → answer_node directly (economy model, no retrieval)

---

## Decision 4: Confidence Fusion Formula

**Decision**: `confidence = (1 - α) × similarity + α × rerank` with **α = 0.7** (precision-focused).

**Rationale**: Tech reference §7 — α=0.7 is for "technical/legal" domains where precision matters more than recall. For an SME sales agent, hallucinating a wrong price is worse than saying "I don't know" (Article IX.2). Threshold = 0.7 matches FR-010 and the existing `CONFIDENCE_THRESHOLD` constant in `services/rag/constants.py`. Min-max scaling applied before fusion when both scores are available.

**Fallback**: If no reranker available (dev/offline with no model loaded), α = 0 → pure similarity score. This preserves the Week 2 behavior.

**Alternatives considered**:
- α=0.3 (recall-focused): rejected — too many low-precision answers for sales context
- α=0.5 (balanced): viable but precision bias justified for cost-sensitive SME context

---

## Decision 5: Checkpointer Strategy

**Decision**: `MemorySaver` for dev/test. `AsyncPostgresSaver` wired but gated behind `ENVIRONMENT=prod` env var.

**Rationale**: Tech reference §1 — MemorySaver for dev local, AsyncPostgresSaver for production (Article VII). Week 3 does not need persistent state across restarts (that's Week 5). But the graph must be designed to accept a checkpointer so Week 5 can swap it in without code changes.

**Alternatives considered**:
- SqliteSaver for dev: viable but adds file dependency; MemorySaver is truly stateless for dev
- Always AsyncPostgresSaver: overkill for Week 3, requires DB to be up for every unit test

---

## Decision 6: Streaming Mode

**Decision**: `updates + messages` combination mode exposed via `graph.astream_events()`.

**Rationale**: Tech reference §6 — `updates + messages` is the recommended combination for "state changes + typing effect". For the debug CLI (`run_agent.py`), each node emits its delta so developers see intent → retrieval → escalation → answer in sequence without waiting for full completion.

For the FastAPI `/query` endpoint (existing in `api/routes/query.py`), streaming is behind an `Accept: text/event-stream` check — not mandatory for Week 3.

---

## Decision 7: Contract Test Stack

**Decision**: `pytest-asyncio` + `respx` (HTTP mock at transport layer).

**Rationale**: Tech reference §8 — respx is recommended for mocking at the HTTP transport layer, which means LiteLLM's httpx client is intercepted without changing any application code. Five required scenarios: 200 OK (valid schema), 404, 429 (backoff validation), 500 (graceful degradation), ReadTimeout.

**Alternatives considered**:
- `unittest.mock` patch: rejected — mocks LiteLLM internals, brittle against version changes
- `httpretty`: rejected — not compatible with asyncio transport

---

## Decision 8: Local Reranker Execution

**Decision**: CrossEncoder via `anyio.to_thread.run_sync` — only if `RERANKER_ENABLED=true` in `.env`. Disabled by default in dev (alpha=0 fallback).

**Rationale**: Tech reference §2 — blocking policy mandates CPU-bound rerankers off the event loop. But downloading a CrossEncoder model adds 400MB+ to dev setup. The reranker is opt-in in Week 3; the confidence fusion code is written to accept `rerank_score=None` and fall back to similarity-only. Week 7 enables the reranker conditionally per environment.

**`RERANKER_ENABLED` restart behavior**: This flag is a **startup-time configuration** read once by `pydantic-settings` when `Settings()` is instantiated. Changing it requires an application restart (or `uvicorn` reload). It CANNOT be toggled mid-session. The application does not hot-reload settings at runtime. Setting `RERANKER_ENABLED=true` without the CrossEncoder model downloaded will cause a `RuntimeError` on first use — not a startup crash. The flag is per-deployment environment (set in `.env` or container env vars), not per-request.

---

## Decision 9: ADR Required

---

## Decision 10: Router Node Must Use `economy-chat` (Not `light-chat`) — Ollama G1

**Decision**: The router node (intent classification) uses `economy-chat` (qwen3-1.7b), **not** `light-chat` (qwen3:0.6b), even though classification is a lightweight task.

**Rationale**: Week 2 documents a critical gotcha (G1): *"Ollama loads one model at a time into VRAM. Switching models mid-request causes VRAM thrashing and OOM."* The retrieval node calls `answer_with_rag()`, which internally calls `economy-chat` for `normalize_query` (step 2) and for answer generation (step 12). If the router node used `light-chat`, every agent turn would cause two VRAM model swaps: `economy-chat → light-chat` (router) → `economy-chat` (retrieval/answer). On SME hardware (4–8GB RAM), this causes latency spikes or OOM crashes. Using `economy-chat` throughout the agent keeps Ollama on a single model for the entire turn.

**Implication for node design**:
- `router_node`: calls `economy-chat` with `response_format=IntentClassification`
- `escalation_node`: pure Python logic — no LLM call (zero model cost)
- `retrieval_node`: calls `answer_with_rag()` which internally calls `economy-chat` (normalize_query) and `economy-embedding` (bge-m3 embed) sequentially
- `answer_node`: calls `economy-chat` or `premium-local-chat` based on `EscalationDecision`
- `premium-local-chat` is only loaded when escalation fires — this model swap is acceptable (intentional escalation, not hot-path)

**Alternatives considered**:
- `light-chat` for router: rejected — model swap on every request (G1 violation)
- `economy-chat` + speculative parallel classify+escalation: rejected — `asyncio.gather` across different Ollama models violates G1

---

## Decision 11: Dual-Layer Confidence Guard — Two Thresholds, Two Purposes

**Decision**: Week 3 has **two independent confidence guards** at different layers with different thresholds. They are not in conflict.

| Layer | Location | Threshold | Metric | Behavior |
|-------|----------|-----------|--------|----------|
| L1 — RAG Tool Guard | Inside `answer_with_rag()` step 10 | `0.45` (existing `CONFIDENCE_THRESHOLD`) | Raw cosine similarity (vector only, no reranker) | Returns `declined=True`, `answer=DECLINE_MESSAGE` — no LLM call |
| L2 — Agent Confidence Node | `confidence_node` in `core/agent/nodes/confidence.py` | `0.70` | Fused score `(1-α)·similarity + α·rerank` | Sets `AgentState.declined=True`, propagates safe fallback |

**Why two guards?**
- The RAG tool (Week 2) guards against queries with truly no relevant data (`similarity < 0.45`). This is a broad filter.
- The agent confidence_node (Week 3) applies a higher standard using the fused score (similarity + rerank). A query can pass the RAG guard (similarity = 0.55 > 0.45) but fail the agent guard if the reranker scores the retrieved chunks poorly (fused < 0.70).
- In dev/offline mode (reranker disabled): fused = similarity (α=0). A query with similarity=0.55 would pass L1 (0.55 > 0.45) but **fail** L2 (0.55 < 0.70). This is intentional — the agent is more conservative than the raw RAG tool alone.

**Confidence node logic when RAG tool already declined**:
```
if rag_result.declined:                          # L1 fired (similarity < 0.45)
    state.declined = True                         # propagate
    state.confidence_score = rag_result.similarity_score
    → skip fused score computation, proceed to END
else:
    compute fused score                           # L2 evaluation
    if fused_score < 0.70:
        state.declined = True
    else:
        state.declined = False
```
This prevents double-computing scores when the RAG tool has already safely declined.

**ADR-002**: "Why LangGraph over manual async loop for agent orchestration"  
- Context: Week 2 uses a linear pipeline (`answer_with_rag`); Week 3 needs branching, state persistence, streaming, and HITL (Week 4)  
- Decision: LangGraph — Article II explicitly exempts it as the mandated orchestration layer  
- Consequences: StateGraph adds ~50ms compile overhead at startup (acceptable); enables interrupt/resume for Week 4 HITL at zero refactor cost  
- Alternatives: Manual `while` loop with match/case — rejected: no checkpointing, no streaming, no interrupt without custom code

**Week 4 HITL interrupt target**: `interrupt_before=["answer_node"]`. Week 3's `escalation_node` is NOT the interrupt target — it is a pure-Python routing node (zero side effects, zero LLM call) with no user-visible action to approve. The meaningful Critical Actions (checkout, order confirmation, final pricing) live in `answer_node` and future `order_node`/`checkout_node` (Week 6+). Week 3 prepares the checkpointer injection point (`build_graph(checkpointer=...)`) so Week 4 can add `interrupt_before` with zero Week 3 code refactoring. The `escalation_node` state output (`EscalationDecision` fields) is stable and will not change in Week 4.

*ADR file to be created at `docs/adr/002-langgraph-orchestration.md` during implementation.*
