# ADR 002: LangGraph for Agentic Workflow Orchestration

**Status**: ACCEPTED  
**Date**: 2026-03-05  
**Authors**: AI Sales Agent Team  
**Deciders**: Engineering, Architecture  

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

**Adopt LangGraph (v0.1.27+) as the orchestration framework.**

LangGraph provides:
1. **Typed StateGraph**: Compile-time state schema validation (via TypedDict) prevents runtime type errors
2. **Conditional Edges**: `add_conditional_edges()` enables intent-driven routing without nested if/else
3. **Command API**: Return `Command(goto=node_name, update=state_delta)` for clean state mutations
4. **Checkpointing**: `AsyncPostgresSaver` for Week 5 multi-turn conversation persistence
5. **Event Streaming**: `astream_events()` v2 API for per-node deltas (FR-006 compliance)
6. **Interrupt Support**: `interrupt_before` for Week 4 human-in-the-loop workflows

## Consequences

### Positive
- **Single Source of Truth**: Graph structure in `core/agent/graph.py` is the canonical state machine definition
- **Type Safety**: Pydantic AgentState catches field mismatches at compile time
- **Testability**: Node functions are pure (state→state dict) — unit testable without mocking the graph
- **Observability**: LangSmith integration via `checkpointer` parameter; OpenTelemetry spans auto-emitted
- **Week 4 Ready**: `interrupt_before` parameter enables HITL workflows without redesign

### Negative
- **Compile Overhead**: `build_graph()` compile takes ~50ms (one-time, CLI startup cost)
  - Mitigated: Compile in `cli/run_agent.py`, cache in tests via `MemorySaver()`
- **State Serialization**: checkpointer requires `AgentState` to serialize (Pydantic-compatible)
  - Mitigated: Use `model_dump()` / `model_validate()` for persistence
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

**Graph Structure** (5 nodes):
```
START → router_node
         ├──→ retrieval_node → confidence_node ──(intent-check)──→ answer_node
         ├──→ escalation_node → answer_node                        ↓
         └──→ answer_node (SMALLTALK direct) ────────────────────→ END
         └──→ escalation_node (COMPLAINT/NEGOTIATION) → answer_node
```

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

**Checkpointer Integration** (Week 5):
```python
from langgraph.checkpoint.postgres import AsyncPostgresSaver

checkpointer = AsyncPostgresSaver(
    sync_connection_string="postgresql://...",  # used for schema init
    async_connection_string="postgresql+asyncpg://...",
)
graph = build_graph(checkpointer=checkpointer)
```

## Related Decisions

- **ADR 001**: Vector database (pgvector) for L2 cache
- **Article II**: Graph-based orchestration requirement
- **Article VI**: LiteLLM-only model calling (no direct SDKs)
- **FR-006**: Per-node event streaming via `astream_events()` v2

## References

- LangGraph Docs: https://langchain-ai.github.io/langgraph/
- LangGraph `AsyncPostgresSaver`: https://langchain-ai.github.io/langgraph/how-tos/persistence/
- LangSmith Integration: https://langchain-ai.github.io/langgraph/how-tos/agent-state/
- Week 3 Spec: `specs/003-agentic-workflow/spec.md` (Article II, FR-006, FR-007)
