# Feature Specification: Agentic Workflow & Safe Logic

**Feature Branch**: `003-agentic-workflow`  
**Created**: 2026-03-01  
**Status**: Draft  
**Input**: User description: "Week 3: Agentic Workflow & Safe Logic — LangGraph orchestration with TypedDict state, async tools, intent-first model escalation, confidence scoring, and contract tests"

## Context & Background

This feature builds the **orchestration layer** on top of the infrastructure (Week 1) and RAG pipeline (Week 2). It transforms the system from a simple retrieval pipeline into a **controllable, state-driven AI sales agent** using LangGraph as the execution core.

Week 1 delivered: async FastAPI, PostgreSQL/pgvector, LiteLLM+Ollama, semantic cache, structured logging.  
Week 2 delivered: hybrid retrieval, adaptive TopK, context compression, confidence threshold, citation metadata.  
Week 3 delivers: typed agent state, intent routing, model escalation, per-node streaming, tool contract tests.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Intent-Classified Sales Response (Priority: P1)

A customer sends a message (e.g., "What is the price of product X?"). The agent classifies the intent, selects the appropriate model tier, retrieves relevant context from the RAG pipeline (Week 2), and returns a grounded, cited answer.

**Why this priority**: This is the core agent loop — everything else depends on it working correctly. Without intent routing and state, there is no agent, only a raw pipeline.

**Independent Test**: Send a Vietnamese product-inquiry message through the agent graph and verify the final response includes a citation, a confidence score, and was served by the economy model.

**Acceptance Scenarios**:

1. **Given** a customer query classified as `INFO_QUERY`, **When** the router node runs, **Then** the economy model is selected and the RAG retrieval tool is invoked.
2. **Given** the RAG tool returns results with confidence ≥ 0.7, **When** the answer node runs, **Then** the response includes the answer text and at least one `ProductID`/`ChunkID` citation.
3. **Given** the agent completes, **When** the state is inspected, **Then** it contains `intent`, `model_used`, `similarity_score`, `escalation_flag`, and `response`.

---

### User Story 2 — Complaint/Negotiation Escalation to Premium Model (Priority: P2)

A customer sends a message expressing dissatisfaction or requesting a discount. The router node detects the sensitive intent and immediately escalates to the premium model, bypassing the economy model regardless of retrieval confidence.

**Why this priority**: This prevents the cheap model from mishandling revenue-sensitive or emotionally charged conversations — a direct business risk for SMEs.

**Independent Test**: Send a message containing complaint or negotiation language and verify the `model_used` field in the final state is the premium model and `escalation_flag` is `true`.

**Acceptance Scenarios**:

1. **Given** a message classified as `COMPLAINT` or `NEGOTIATION`, **When** the escalation node evaluates intent, **Then** the premium model is selected without checking the similarity score.
2. **Given** escalation occurs, **When** the trace is recorded in `model_trace`, **Then** the reason field reads `intent_escalation` (not `low_confidence`).
3. **Given** a `COMPLAINT` query, **When** the answer is generated, **Then** the tone is empathetic and no hallucinated policy text appears in the response.

---

### User Story 3 — Low-Confidence Fallback Guard (Priority: P2)

When retrieval confidence is below 0.7, the agent does not attempt to answer. Instead, it returns a safe fallback message ("I couldn't find relevant information") and logs the event for human review.

**Why this priority**: Prevents hallucination from reaching customers. This is a safety gate that must exist before Week 4's HITL layer can be meaningful.

**Independent Test**: Submit a query with no relevant product data in the DB and verify the response is the safe fallback string and no model escalation occurs.

**Acceptance Scenarios**:

1. **Given** vector search returns similarity < 0.7, **When** the confidence guard node evaluates the score, **Then** the agent returns the fallback message without calling the LLM.
2. **Given** the fallback triggers, **When** the state is stored, **Then** `escalation_flag` is `false` and `model_used` is `null`.

---

### User Story 4 — Per-Node Streaming for Debuggability (Priority: P3)

A developer running the agent locally sees step-by-step output as each LangGraph node executes — intent classification result, retrieval hits, model selection decision, final answer — without waiting for full completion.

**Why this priority**: Critical for development iteration speed and debugging production issues. Does not affect end-user behavior but directly impacts maintainability.

**Independent Test**: Run the graph with streaming enabled and verify at least 4 distinct event types are emitted (router, retrieval, escalation, answer) before the final response.

**Acceptance Scenarios**:

1. **Given** streaming mode is active, **When** the graph executes, **Then** each node emits a structured event containing `node_name`, `state_snapshot`, and `timestamp`.
2. **Given** the graph runs end-to-end, **When** events are collected, **Then** they can be replayed to reconstruct the exact execution path.

---

### User Story 5 — Tool Contract Tests Prevent Integration Regressions (Priority: P3)

Before any tool (inventory lookup, order status) is wired into the agent, a contract test exists that validates the tool's input schema and output schema against the LangGraph state type. If the tool changes its signature, the contract test fails immediately.

**Why this priority**: Prevents silent contract violations where a tool change breaks the agent without a clear error message. Cheap to write, expensive to skip.

**Independent Test**: Modify a mock tool's return type to violate the Pydantic schema and verify the contract test fails with a descriptive error.

**Acceptance Scenarios**:

1. **Given** a tool is registered in the agent, **When** the contract test runs, **Then** it validates that the tool's output conforms to the expected Pydantic model.
2. **Given** a breaking change is introduced in a tool's return type, **When** tests run, **Then** the contract test fails before any graph execution occurs.

---

### Edge Cases

- What happens when intent classification returns an unknown/unsupported intent type?
- How does the agent handle a timeout from a retrieval tool mid-graph?
- What if the premium model is unavailable (offline/API error) during escalation?
- What if `model_trace` write fails — does the agent still return a response?
- How does the graph behave when the same message triggers both `COMPLAINT` and `INFO_QUERY` signals?

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The agent state MUST be a pure serializable `TypedDict` containing at minimum: `session_id`, `user_message`, `intent`, `similarity_score`, `rerank_score`, `model_used`, `escalation_flag`, `response`, `citations`, and `conversation_history`.
- **FR-002**: All graph nodes MUST be implemented as async functions; no synchronous blocking calls are permitted except local ML utilities (e.g., cross-encoder reranker) offloaded to a thread executor.
- **FR-003**: All tool input and output schemas MUST be defined as Pydantic models; no `dict` or `str` returns are acceptable from tools.
- **FR-004**: The compiled graph MUST export a Mermaid execution diagram as a static artifact (file or stdout) to document the agent's control flow.
- **FR-005**: The router node MUST classify intent into at least four categories: `INFO_QUERY`, `COMPLAINT`, `NEGOTIATION`, `SMALLTALK`; classification MUST use the economy LiteLLM model with a Pydantic-structured output.
- **FR-006**: The agent MUST support per-node streaming, emitting a structured event (node name, partial state) at each graph step.
- **FR-007**: The model escalation node MUST apply intent-first logic: if intent is `COMPLAINT` or `NEGOTIATION`, escalate to premium model unconditionally; otherwise escalate only if `similarity_score < 0.7`.
- **FR-008**: The confidence scoring node MUST store `similarity_score`, `rerank_score`, `model_used`, and `escalation_flag` in both the agent state and the `model_trace` DB table after each run.
- **FR-009**: Contract tests MUST exist for every registered tool before the tool's implementation is complete; tests validate input schema, output schema, and error behavior.
- **FR-010**: If `similarity_score < 0.7` and intent is `INFO_QUERY`, the agent MUST return a safe fallback message and MUST NOT invoke the LLM.

### Key Entities

- **AgentState**: The canonical TypedDict representing a single agent run's full lifecycle state; immutable between nodes except via explicit state updates.
- **IntentClassification**: Pydantic model output of the router node; contains `intent` enum, `confidence`, and `raw_classification`.
- **ToolContract**: Pydantic input/output schema pair that each registered tool must implement; used by contract tests as the source of truth.
- **ModelTrace**: DB record written after each agent run; stores model used, escalation reason, scores, latency, and token count for cost analysis.
- **EscalationDecision**: Pydantic model capturing the escalation node's output: `escalate` (bool), `reason` (enum: `intent_escalation` | `low_confidence`), `selected_model`.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of graph runs produce a fully populated `AgentState` with no `None` values in required fields (`intent`, `model_used`, `escalation_flag`, `response`).
- **SC-002**: `COMPLAINT` and `NEGOTIATION` intents are escalated to the premium model in 100% of cases, regardless of similarity score.
- **SC-003**: Queries with `similarity_score < 0.7` return the safe fallback in under 200ms (no LLM call penalty).
- **SC-004**: Per-node streaming emits at least one event per node, enabling full execution replay from streamed output alone.
- **SC-005**: All contract tests pass before any tool integration test or end-to-end test runs.
- **SC-006**: The compiled graph Mermaid diagram accurately reflects all nodes and conditional edges in the actual execution graph.
- **SC-007**: 0 instances of unstructured string parsing (regex, `json.loads` on raw LLM output) anywhere in the agent codebase.

---

## Assumptions

- Week 1 and Week 2 deliverables are complete and available: async DB session, LiteLLM proxy configured for both Ollama (local) and API models, hybrid search returning results with confidence scores and citation metadata.
- The `model_trace` table from Week 1's DB schema is available for writing escalation records.
- Intent classification runs on the economy model (Ollama local in dev, cheapest API model in staging); premium model is configurable via environment variable — not hardcoded.
- "Premium model" and "economy model" refer to LiteLLM model aliases defined in config, not specific vendor models.
- Streaming is delivered over the same in-process interface used for development/testing; Telegram webhook streaming integration is deferred to Week 6.
- The `SMALLTALK` intent routes directly to the economy model and bypasses RAG retrieval entirely (chitchat does not need product context).

---

## Out of Scope

- Telegram webhook integration (Week 6)
- Human-in-the-loop pause/resume (Week 4)
- Persistent conversation memory across restarts (Week 5)
- Load testing or rate limiting (Week 7)
- Any frontend or admin UI
