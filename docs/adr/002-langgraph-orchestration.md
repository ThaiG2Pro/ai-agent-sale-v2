# ADR 002: LangGraph for Agentic Workflow Orchestration

**Status**: ACCEPTED  
**Date**: 2026-03-05  
**Author**: Thái Hoàng (solo project)  
**Last reviewed**: 2026-10-02 — implementation notes updated to the current graph  

> **Terminology.** "Article N" references point to the project's original engineering
> charter (`docs/specs/*/plan.md`, "Constitution"): II = graph-based orchestration & simplicity,
> V = async I/O only, VI = all model calls through LiteLLM, VII = single database, X = cost /
> image-size discipline. They are kept for traceability to the spec-kit era.

## Context

**Week 2 Limitation:**  
The Week 2 RAG pipeline was linear and sequential:
```
User Query → Normalize → Cache Check → Embed → Search → Compress → Answer (LLM) → Response
```
This design prevented conditional routing, intent-based branching, and escalation workflows required by Week 3 requirements (FR-007: intent-driven escalation, US2: sensitive query handling, Article II: graph-based orchestration).

**Week 3 Requirement:**  
Week 3 must support:
- Intent classification → dynamic routing (INFO_QUERY→retrieval, COMPLAINT→escalation, SMALLTALK→direct answer)
- Confidence-based gates (Layer 1/Layer 2 guards)
- Escalation logic (borderline queries → premium model selection)
- Streaming per-node events for real-time UI updates
- State persistence across branching paths

**Article II Mandate:**  
> "The agent orchestration MUST implement a graph-based state machine with explicit edges and conditional routing, not hand-written while loops or if-else chains."

## Decision

**Adopt LangGraph as the orchestration framework** (adopted at v0.1.27; the project now pins `langgraph>=0.3`).

LangGraph provides:
1. **Typed StateGraph**: `AgentState` is a `TypedDict` (`core/agent/state.py`); the graph is built as `StateGraph(AgentState)` so node outputs are checked against one schema
2. **Conditional Edges**: `add_conditional_edges()` enables intent-driven routing without nested if/else
3. **Command API**: Return `Command(goto=node_name, update=state_delta)` for clean state mutations
4. **Checkpointing**: `AsyncPostgresSaver` for Week 5 multi-turn conversation persistence
5. **Event Streaming**: `astream_events()` v2 API for per-node deltas (FR-006 compliance)
6. **Interrupt Support**: `interrupt()` inside `hitl_guard_node` pauses the run for human review (HITL)

## Consequences

### Positive
- **Single Source of Truth**: Graph structure in `core/agent/graph.py` is the canonical state machine definition
- **Type Safety**: the `AgentState` TypedDict gives static (pyright/IDE) checking of node inputs and outputs
- **Testability**: Node functions are pure (state→state dict) — unit testable without mocking the graph
- **Observability**: every node is wrapped by `traced_node()` and emits an OpenTelemetry span (`node.<name>`) into Phoenix
- **HITL ready**: `interrupt()` + the Postgres checkpointer enable pause/resume without redesign

### Negative
- **Compile Overhead**: `build_graph()` compile takes ~50ms (one-time, CLI startup cost)
  - Mitigated: Compile in `cli/run_agent.py`, cache in tests via `MemorySaver()`
- **State Serialization**: the checkpointer must serialize every `AgentState` field
  - Mitigated: plain JSON-able types only; `JsonPlusSerializer(pickle_fallback=False)` (no pickle)
- **Node Isolation**: Graph nodes cannot share mutable state; must flow through state dict
  - Mitigated: This is intentional (Article II mandates stateless logic)
- **Debugging**: Multi-branch graphs harder to trace than linear pipelines
  - Mitigated: `astream_events()` provides per-node execution visibility

## Alternatives Considered

### 1. **Manual while-loop orchestration**
```python
state = initial_state()
while state['node'] != 'end':
    if state['node'] == 'router':
        state = router_node(state)
    elif state['node'] == 'retrieval':
        state = retrieval_node(state)
    ...
```

**Rejected because:**
- Violates Article II (not graph-based)
- If/else chains are unmaintainable with 5+ nodes
- No type safety — state mutations silent
- No conditional edge validation
- Streaming requires custom event emission

### 2. **Pydantic AI + local models**
Pydantic AI offers agent scaffolding but:
- Lock-in to Pydantic's model routing (not flexible for Week 4 HITL)
- No checkpointing support
- Requires direct LLM SDK imports (violates Article VI: LiteLLM-only)

**Rejected.**

### 3. **FastAPI background tasks + Redis**
- Add external dependency (Redis) — violates zero-cost-first
- No state persistence between requests
- Polling complexity for event streaming

**Rejected.**

## Implementation Notes

**Graph Structure** — original week-3 shape (5 nodes):
```
START → router_node
         ├──→ retrieval_node → confidence_node ──(intent-check)──→ answer_node → END
         ├──→ escalation_node (COMPLAINT/NEGOTIATION) → answer_node
         └──→ answer_node (SMALLTALK direct)
```

**Current graph** (13 nodes, registered in `_NODE_FUNCS` in `core/agent/graph.py`; the
`GRAPH_NODES` set is derived from it so tracing can never miss a node):
`router_node`, `retrieval_node`, `memory_retrieval_node`, `confidence_node`, `clarify_node`,
`escalation_node`, `answer_node`, `hitl_guard_node`, `order_execution_node`, `cancellation_node`,
`customer_support_node`, `queue_consumer_node`, `state_freshness_validator_node`.
The README's mermaid diagram shows the current routing.

**State Mutation Pattern:**
```python
async def router_node(state: AgentState) -> Command:
    classification = await AIGateway.complete(...)
    return Command(
        goto="retrieval_node",  # conditional routing
        update={
            "intent": classification.primary_intent.value,
            "secondary_intents": [...],
        }
    )
```

**Checkpointer Integration** (`core/agent/checkpointer.py`):
```python
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

async def create_checkpointer(dsn: str) -> AsyncPostgresSaver:
    async with AsyncPostgresSaver.from_conn_string(dsn) as setup_saver:
        await setup_saver.setup()            # autocommit: CREATE INDEX CONCURRENTLY
    pool = AsyncConnectionPool(dsn, ...)     # psycopg3 pool for runtime
    return AsyncPostgresSaver(pool, serde=JsonPlusSerializer(pickle_fallback=False))
```
`thread_id = session_id`; the checkpointer uses psycopg3 while the app's own tables use asyncpg.

## Related Decisions

- **ADR 001**: Core stack (PostgreSQL + pgvector, LiteLLM, async SQLAlchemy)
- **Article II**: Graph-based orchestration requirement
- **Article VI**: LiteLLM-only model calling (no direct SDKs)
- **FR-006**: Per-node event streaming via `astream_events()` v2

## References

- LangGraph Docs: https://langchain-ai.github.io/langgraph/
- LangGraph `AsyncPostgresSaver`: https://langchain-ai.github.io/langgraph/how-tos/persistence/
- LangSmith Integration: https://langchain-ai.github.io/langgraph/how-tos/agent-state/
- Week 3 Spec: `docs/specs/003-agentic-workflow/spec.md` (Article II, FR-006, FR-007)
