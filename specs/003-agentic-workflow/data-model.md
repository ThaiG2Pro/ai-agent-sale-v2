# Data Model: Agentic Workflow & Safe Logic (Week 3)

**Phase 1 — Entities, State, and DB Changes**  
**Date**: 2026-03-03

---

## 1. AgentState (TypedDict — core/agent/state.py)

The canonical runtime state for a single agent turn. Serializable to JSONB. Immutable between nodes (mutated only via state update returns).

```
AgentState
├── session_id: str                   # UUID7, links to conversation_sessions
├── user_message: str                 # Raw input from user
├── messages: Annotated[list, add_messages]  # Full message history (dedup by ID)
│                                            # NOTE: this IS "conversation_history" —
│                                            # do NOT add a separate conversation_history field
├── intent: str | None                # Primary classified intent enum value
├── secondary_intents: list[str]      # Additional detected intents (default=[]);
│                                     # escalation checks ALL intents for COMPLAINT/NEGOTIATION
├── intent_confidence: float          # Classification confidence (0.0–1.0)
├── retrieved_chunks: list[dict]      # Raw chunks from RAG tool
├── citations: list[dict]             # [{product_id, chunk_id, sku, name}]
├── similarity_score: float           # Best vector cosine score (0.0–1.0)
├── rerank_score: float | None        # CrossEncoder score if available
├── confidence_score: float           # Fused score: (1-α)·similarity + α·rerank
├── model_used: str | None            # LiteLLM model alias used for generation
├── escalation_flag: bool             # True if premium model was selected
├── escalation_reason: EscalationReasonEnum | None  # use Enum: INTENT_ESCALATION | LOW_CONFIDENCE | NONE
├── escalation_failure: bool          # True if premium model was unavailable and
│                                     # economy model was used as fallback (FR-007)
├── response: str | None              # Final answer text
├── declined: bool                    # True if confidence guard fired
└── error: str | None                 # Error message for dead-letter logging
```

**Key reducers**:
- `messages`: `add_messages` (ID-based dedup, safe for retries)
- `citations`: `operator.add` (accumulate across retrieval + answer nodes)
- All other fields: default overwrite (last write wins)

---

## 2. Pydantic Boundary Models (core/agent/state.py)

These models validate data crossing the LLM ↔ Application boundary (Article VI).

### IntentClassification

Output of the router node's LLM call.

```
IntentClassification
├── primary_intent: IntentEnum     # Primary intent: INFO_QUERY | PRICING | COMPARISON |
│                                  #                 COMPLAINT | NEGOTIATION | SMALLTALK | AVAILABILITY
├── secondary_intents: list[IntentEnum]  # Additional detected intents (default=[])
│                                  # FR-007: escalation fires if ANY intent is
│                                  # COMPLAINT or NEGOTIATION
├── confidence: float              # 0.0–1.0, from logprobs or model self-report
└── reasoning: str                 # Short explanation (for trace/debug)
```

### EscalationDecision

Output of the escalation node's logic.

```
EscalationDecision
├── escalate: bool             # Whether to use premium model
├── reason: EscalationReasonEnum   # "intent_escalation" | "low_confidence" | "none"
└── selected_model: str        # LiteLLM model alias: "economy-chat" | "premium-local-chat" | "premium-chat"
```

### ToolInput / ToolOutput (per tool — see contracts/)

Each registered tool has a strict Pydantic input and output model. See `contracts/` directory.

---

## 3. Intent Routing Map

```
IntentEnum value       →  Route
─────────────────────────────────────────────────────────
COMPLAINT              →  escalation_node (immediate escalation)
NEGOTIATION            →  escalation_node (immediate escalation)
INFO_QUERY             →  retrieval_node → confidence_node → answer_node
PRICING                →  retrieval_node → confidence_node → answer_node
COMPARISON             →  retrieval_node → confidence_node → answer_node
AVAILABILITY           →  retrieval_node → confidence_node → answer_node
SMALLTALK              →  answer_node (economy-chat, no retrieval)
```

---

## 4. Confidence Score Computation

### Dual-Layer Guard Architecture

There are **two independent confidence guards** operating at different layers. They are not in conflict.

```
Layer 1 — RAG Tool Guard (Week 2, existing)
  Location: inside answer_with_rag() step 10
  Threshold: CONFIDENCE_THRESHOLD = 0.45  (raw cosine similarity, no reranker)
  Fires:     best_similarity < 0.45 OR compressed_chunks == 0
  Returns:   RAGSearchOutput(declined=True, answer=DECLINE_MESSAGE, similarity_score<0.45)

Layer 2 — Agent Confidence Node (Week 3, new)
  Location: confidence_node in core/agent/nodes/confidence.py
  Threshold: 0.70  (fused score: similarity + optional rerank)
  Fires:     if Layer 1 already fired → propagate declined=True immediately
             else: compute fused_score; if fused_score < 0.70 → declined=True
```

**Dev mode (reranker disabled, α=0)**:
- A query with similarity=0.55 passes Layer 1 (0.55 > 0.45) but fails Layer 2 (0.55 < 0.70)
- The agent is intentionally more conservative than the raw RAG pipeline

**Dual-layer ordering constraint**: `AGENT_CONFIDENCE_THRESHOLD` (L2) MUST always be configured **strictly greater than** `CONFIDENCE_THRESHOLD` (L1 = 0.45). If L2 ≤ L1, every result that passes L1 automatically passes L2, making the agent confidence guard meaningless. Valid configuration: `L2 ∈ (0.45, 1.0]`. This constraint is enforced via `Field(gt=0.45, le=1.0)` in `core/config.py`.

**Confidence node fast-path when RAG tool already declined**:
```
if rag_result.declined:
    state.declined = True
    state.confidence_score = rag_result.similarity_score
    → skip fused computation → route to END (safe fallback)
```

### Fused Score Computation (when RAG tool accepted)

```
Inputs:
  similarity_score: float   # From pgvector cosine search (Layer 1 passed, so ≥ 0.45)
  rerank_score: float | None  # CrossEncoder (optional, opt-in via RERANKER_ENABLED)
  α = 0.7  (precision-focused, SME sales domain — tech reference §7)

If rerank_score is not None:
  # Min-max scaling before fusion to equalize score ranges
  confidence = (1 - 0.7) × similarity_score + 0.7 × rerank_score

If rerank_score is None (dev default, RERANKER_ENABLED=false):
  confidence = similarity_score  # α = 0 fallback

Agent guardrail:
  if confidence_score < 0.70:  # Layer 2
    → declined = True
    → response = DECLINE_MESSAGE (from services/rag/constants.py)
  else:
    → proceed to answer_node
```

---

## 5. Graph Topology (LangGraph nodes + edges)

```
START
  └─→ router_node
        ├─ COMPLAINT/NEGOTIATION ──→ escalation_node ──→ answer_node ──→ END
        ├─ INFO_QUERY/PRICING/
        │  COMPARISON/AVAILABILITY ─→ retrieval_node
        │                               └─→ confidence_node
        │                                     ├─ declined=True ───────────────→ answer_node (DECLINE_MESSAGE)
        │                                     ├─ INFO_QUERY + 0.45≤sim<0.7 ──→ escalation_node → answer_node
        │                                     └─ otherwise ──────────────────→ answer_node (economy model)
        └─ SMALLTALK ─────────────────────────────────────────────────────────→ answer_node ──→ END
```

**Edge logic from `confidence_node`** (conditional):
```python
def _route_after_confidence(state: AgentState) -> str:
    if state["declined"]:
        return "answer_node"
    if state["intent"] == "INFO_QUERY" and state["similarity_score"] < 0.7:
        return "escalation_node"  # borderline → try premium (FR-007)
    return "answer_node"
```

**Node responsibilities**:
- `router_node`: calls **`economy-chat`** (not `light-chat` — see research Decision 10: Ollama G1 one-model-at-a-time constraint) with `response_format=IntentClassification` Pydantic output. Uses `primary_intent` for routing goto; stores both `primary_intent` and `secondary_intents` in state. Returns `Command(goto=next_node, update={"intent": ..., "secondary_intents": [...], "intent_confidence": ...})`.
- `escalation_node`: pure Python logic (zero LLM call) — checks `state["intent"]` AND `state["secondary_intents"]` (FR-007: escalate if ANY intent is COMPLAINT/NEGOTIATION). Updates `escalation_flag`, `escalation_reason`, `model_used`.
- `retrieval_node`: calls `rag_search` tool → populates `retrieved_chunks`, `citations`, `similarity_score`. If tool returns `declined=True` (Layer 1 guard at 0.45 fired), propagates `declined=True` to state immediately.
- `confidence_node`: pure Python — if `state.declined` already True (from Layer 1), routes to `answer_node` immediately. Otherwise fuses similarity + rerank scores; applies Layer 2 guard at 0.70. Updates `confidence_score`, `declined`. **Conditional routing**: if `intent == INFO_QUERY` AND `0.45 ≤ similarity_score < 0.7` AND NOT declined → routes to `escalation_node` (borderline escalation, FR-007); otherwise → `answer_node` directly.
- `answer_node`: **universal terminal node** — handles declined (`DECLINE_MESSAGE`, `model_used` stays `None` in state but `intended_model` recorded in trace metadata_), and accepted (LiteLLM call) paths. Always writes `model_trace` (FR-008 compliance). Returns final `response`.

---

## 6. Database Changes (Week 3 scope)

**No new tables required.** All existing tables from Week 1 are used as-is.

| Table | Week 3 Usage |
|-------|-------------|
| `agent_v1.model_traces` | Write after each agent run — existing `_write_model_trace()` called from answer_node. Add `escalation_reason` to `metadata_` JSONB field. |
| `agent_v1.conversation_messages` | Read for context (Week 5 adds write). `source_chunk_ids` field populated from citations. |
| `agent_v1.conversation_sessions` | Read `session_id` for checkpointer thread. |
| `agent_v1.semantic_cache` | Read-only in Week 3 (cache writes handled by existing RAG pipeline). |

**ModelTrace metadata_ additions** (no schema migration needed — JSONB):
```json
{
  "guard_decision": "ACCEPTED|REJECTED",
  "best_similarity": 0.87,
  "similarity_gap": 0.12,
  "top_k_used": 5,
  "query_category": "short",
  "escalation_reason": "intent_escalation|low_confidence|none",
  "intent": "COMPLAINT"
}
```

---

## 7. Tool Registry (core/agent/tools.py)

| Tool | Input Model | Output Model | Implementation |
|------|-------------|--------------|----------------|
| `rag_search` | `RAGSearchInput` | `RAGSearchOutput` | Wraps `answer_with_rag()` from Week 2 — uses `make_rag_tool(db)` factory |
| `inventory_lookup` | `InventoryLookupInput` | `InventoryLookupOutput` | **Stub only** in Week 3 — returns mock data; contract test validates schema. **Week 6**: replace stub body with real ERP call using `make_inventory_tool(db: AsyncSession)` factory — input/output schemas MUST NOT change. |

Contract details in `contracts/rag_tool.md` and `contracts/inventory_tool.md`.

### DB Session Injection Pattern

`answer_with_rag(db, query, model)` requires an `AsyncSession`. LangGraph tools are plain async functions — they do not auto-receive DB sessions. Tools are built as **closures** that capture `db` from the agent's invocation context:

```
# Pattern (tool factory, captures db from graph invocation)
def make_rag_tool(db: AsyncSession):
    @tool
    async def rag_search(input: RAGSearchInput) -> RAGSearchOutput:
        result = await answer_with_rag(db, input.query, input.model)
        return RAGSearchOutput.from_rag_result(result)
    return rag_search

# In graph invocation:
tools = [make_rag_tool(db)]
agent = graph.compile(tools=tools, checkpointer=checkpointer)
```

This ensures each graph invocation gets its own DB session, consistent with the existing `Depends(get_db)` pattern in FastAPI.

**Session lifetime in FastAPI**: `db` MUST be a per-request `AsyncSession` created by `Depends(get_db)`. It is NOT a connection pool or a module-level singleton. Because tools capture `db` via closure, the graph MUST be compiled (`.compile()`) per request — the compiled graph is NOT a singleton. Caching a compiled graph with a closed `AsyncSession` inside its tools will cause `asyncpg` "connection closed" errors under concurrency.

**Canonical injection pattern — applies to ALL tools**: The `make_xxx_tool(db: AsyncSession)` factory closure is the **canonical DB-session injection pattern for all current and future tools**, including the real `inventory_lookup` implementation in Week 6. Week 6 implementers MUST use `make_inventory_tool(db: AsyncSession)` and NOT inject sessions via global state, module-level imports, or alternative DI patterns. Deviating from this pattern breaks session isolation guarantees under concurrent FastAPI requests.

**Week 3 stub exception**: The Week 3 `inventory_lookup` stub does not require a DB session and DOES NOT use a factory (it always returns mock data). However, when Week 6 replaces it with a real ERP integration that requires DB access, it MUST be refactored to follow the `make_inventory_tool(db)` factory pattern. Task T043 includes a docstring note flagging this expectation.

### Intent Schema Clarification: Two Separate Schemas

The agent uses **two distinct intent schemas** that must not be confused:

| Schema | Used By | Intents | Location |
|--------|---------|---------|----------|
| `IntentClassification` (Week 3, new) | Agent router node | INFO_QUERY, PRICING, COMPARISON, COMPLAINT, NEGOTIATION, AVAILABILITY, SMALLTALK | `core/agent/state.py` |
| `NormalizedQuery.intent` (Week 2, existing) | Inside `answer_with_rag()` step 2 | INFO_QUERY, PRICING, COMPARISON, COMPLAINT, NEGOTIATION, AVAILABILITY, OTHER | `services/ai.py` |

**Why SMALLTALK is safe**: For `SMALLTALK` intent, the agent routes directly to `answer_node`, bypassing the retrieval node entirely. Since `answer_with_rag()` is never called for SMALLTALK, `NormalizedQuery` is never invoked — no schema conflict.

**Why OTHER maps to INFO_QUERY**: The router node consolidates `OTHER` → `INFO_QUERY` in the routing map. Any query that normalizes to OTHER internally becomes a standard retrieval attempt.
