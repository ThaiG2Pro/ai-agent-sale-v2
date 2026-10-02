# Feature Specification: Agentic Workflow & Safe Logic

**Feature Branch**: `003-agentic-workflow`  
**Created**: 2026-03-01  
**Status**: Draft  
**Input**: User description: "Week 3: Agentic Workflow & Safe Logic — LangGraph orchestration with TypedDict state, async tools, intent-first model escalation, confidence scoring, and contract tests"

## Context & Background

This feature builds the **orchestration layer** on top of the infrastructure (Week 1) and RAG pipeline (Week 2). It transforms the system from a simple retrieval pipeline into a **controllable, state-driven AI sales agent** using LangGraph as the execution core.

**Constitution Alignment**: This spec extends the **SME Pro 2026 Constitution** (project root). Key articles enforced in Week 3:
- **Article I**: Core business logic in `core/agent/`; CLI exemption for debug scripts.
- **Article II**: LangGraph explicitly permitted for orchestration.
- **Article III**: TDD-first; contract tests written before implementation.
- **Article IV**: Integration-first testing; graph topology documented in data-model.md.
- **Article V**: No blocking I/O in event loop.
- **Article IX**: Citations grounded with `source_text`.
- **Article X**: Recursion limit 5 turns.
- **Article XII**: Cost-aware routing; escalate only when necessary.

> Refer to Constitution (root) for full text. This spec does NOT duplicate — it extends Constitution with Week 3 functional requirements (FR-001–FR-010).

**Week progression**:  
Week 1 delivered: async FastAPI, PostgreSQL/pgvector, LiteLLM+Ollama, semantic cache, structured logging.  
Week 2 delivered: hybrid retrieval, adaptive TopK, context compression, confidence threshold, citation metadata.  
Week 3 delivers: typed agent state, intent routing, model escalation, per-node streaming, tool contract tests.

---

## Clarifications

### Session 2026-03-01
- Q: Format for the `citations` field in the `AgentState`? → A: List of `Citation` Pydantic models (containing `product_id`, `chunk_id`, `source_text`).
- Q: Handling of multi-intent detection (e.g., INFO + COMPLAINT)? → A: Priority-based coverage; higher-risk intents (COMPLAINT/NEGOTIATION) dictate model tier.
- Q: Per-node streaming event format? → A: Each node emits a structured snapshot containing `node_name` and `state_snapshot` upon completion.
- Q: Handling of premium model unavailability during escalation? → A: Graceful fallback to economy model with `escalation_failure` flag logged in `model_trace`.
- Q: Model mapping in local development (0 VND goal)? → A: Both tiers map to local Ollama; distinguish by model parameters (e.g., qwen2.5-3b vs qwen2.5-7b).

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

**Independent Test**: Submit a query with no relevant product data in the DB (similarity < 0.45, triggers Layer 1 decline) and verify the response is the safe fallback string and no model escalation occurs. **Note**: FR-007 INFO_QUERY escalation only applies to the *borderline* range (0.45 ≤ similarity < 0.7); a similarity below Layer 1 threshold (< 0.45) causes an immediate decline before any escalation logic runs.

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

- What happens when intent classification returns an unknown/unsupported intent type? (Default to `INFO_QUERY`).
- How does the agent handle a timeout from a retrieval tool mid-graph? Set `state["error"] = "RETRIEVAL_TIMEOUT"`, set `state["declined"] = True`, return `DECLINE_MESSAGE` as response — the same DECLINE_MESSAGE used for low-confidence guards. The `model_trace` MUST still be written with `guard_decision="REJECTED"` and `error="RETRIEVAL_TIMEOUT"` in `metadata_`.
- What if the premium model is unavailable? (Gracefully fallback to economy model; set `escalation_failure` flag).
- What if `model_trace` write fails — does the agent still return a response? (Yes, log error to stderr and continue).
- How does the graph behave when the same message triggers both `COMPLAINT` and `INFO_QUERY` signals? (Priority logic applies: `COMPLAINT` wins).

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The agent state MUST be a pure serializable `TypedDict` containing at minimum: `session_id`, `user_message`, `intent` (primary intent str), `secondary_intents` (list[str], default=[]), `intent_confidence` (float), `retrieved_chunks` (list[dict]), `similarity_score` (float), `rerank_score` (float | None), `confidence_score` (float), `model_used` (str | None), `escalation_flag` (bool), `escalation_failure` (bool), `escalation_reason` (str | None, enum value), `response` (str | None), `citations` (list), and `messages: Annotated[list, add_messages]` (full conversation history with LangGraph ID-based dedup reducer). Note: `messages` is the canonical field name for conversation history — `conversation_history` is NOT a separate field.
- **FR-002**: All graph nodes MUST be implemented as async functions; no synchronous blocking calls are permitted except local ML utilities (e.g., cross-encoder reranker) offloaded to a thread executor.
- **FR-003**: All tool input and output schemas MUST be defined as Pydantic models; no `dict` or `str` returns are acceptable from tools.
- **FR-004**: The compiled graph MUST export a Mermaid execution diagram as a static artifact (file or stdout) to document the agent's control flow.
- **FR-005**: The router node MUST classify intent into at least **seven** categories: `INFO_QUERY`, `PRICING`, `COMPARISON`, `COMPLAINT`, `NEGOTIATION`, `SMALLTALK`, `AVAILABILITY`; classification MUST support multi-intent detection and use the economy LiteLLM model with a Pydantic-structured output. (See T012 for `IntentEnum` definition.)
- **FR-006**: The agent MUST support per-node streaming, emitting a structured event (`NodeStreamEvent`) containing `node_name`, `state_snapshot`, and `timestamp` (ISO 8601) upon each node's completion. `state_snapshot` is a **delta** — the dict returned by the node (only changed fields), NOT a full `AgentState` copy; this keeps webhook payload size proportional to what each node actually modifies. The `NodeStreamEvent` schema (fields: `node_name: str`, `state_snapshot: dict`, `timestamp: str`) is **schema-frozen until at least Week 6**; the Telegram webhook integration (Week 6) will add a transport wrapper layer around `NodeStreamEvent` rather than modifying its fields. `astream_agent(message, session_id, db, checkpointer)` parameter names are stable through Week 5; new optional keyword-only parameters MAY be added in Week 5/6 but existing positional parameters MUST NOT change.
- **FR-007**: The model escalation node MUST apply intent-first priority logic with the following full escalation matrix for all 7 intents:
  | Intent | Escalation Rule |
  |---|---|
  | `COMPLAINT`, `NEGOTIATION` | **Always premium** — unconditional, bypasses retrieval entirely |
  | `INFO_QUERY` | **Premium if borderline** — only when `0.45 ≤ similarity_score < 0.7` (passed Layer 1, but borderline for Layer 2); `confidence_node` MUST conditionally route to `escalation_node` before `answer_node` in this case |
  | `PRICING`, `COMPARISON`, `AVAILABILITY` | **Economy always** — RAG path but no score-based escalation |
  | `SMALLTALK` | **Economy always** — no retrieval, no escalation |

  If the premium model is unavailable, the system MUST fallback to the economy model, set `escalation_failure: bool = True` in `AgentState`, and record `escalation_failure` in `model_trace.metadata_`. **Environment note**: the `escalation_failure` fallback logic is environment-neutral — LiteLLM handles HTTP 429/500 from a real API and an unavailable Ollama model identically via its retry/fallback chain. No environment-specific retry_policy is required at the agent layer; configure retry counts in `LITELLM_CONFIG` per environment if needed.
- **FR-008**: The answer node MUST write `model_trace` after **every** execution path (both accepted AND declined) — this is the **universal trace point**. Store `similarity_score`, `rerank_score`, `model_used`, `escalation_flag`, `escalation_failure`, and all escalation metadata in `metadata_` JSONB. Trace write failure MUST NOT block response (fail-safe: catch exception and log to stderr).
- **FR-009**: Contract tests MUST exist for every registered tool before the tool's implementation is complete; tests validate input schema, output schema, and error behavior.
- **FR-010**: **Final confidence guard** (applies AFTER escalation and LLM answer generation): If the fused `confidence_score < 0.70` (Layer 2 guard threshold, uses fused score = `(1-0.7)·similarity + 0.7·rerank` = `0.3·similarity + 0.7·rerank` when rerank is available, or just `similarity` if no rerank), the agent MUST check: if answer was generated and confidence is still low, prepend a disclaimer or return DECLINE_MESSAGE. **Score terminology clarification**: 
  - `similarity_score` (raw cosine, pre-rerank) — used by FR-007 to decide escalation routing (Layer 1 at 0.45, borderline at < 0.7)
  - `confidence_score` (fused post-rerank) — used by FR-010 final check (Layer 2 at 0.70) to ensure answer quality
  - **For INFO_QUERY borderline escalation (FR-007 + FR-010 interaction)**: Escalation_node is called if similarity < 0.7; answer_node invokes premium model and computes final fused confidence_score; if final score is STILL < 0.70 after premium answer, apply disclaimer or return DECLINE_MESSAGE.

### Key Entities

- **AgentState**: The canonical TypedDict representing a single agent run's full lifecycle state; immutable between nodes except via explicit state updates.
- **Citation**: Pydantic model representing a grounded source; contains `product_id`, `chunk_id`, and `source_text`.
- **IntentClassification**: Pydantic model output of the router node; contains `primary_intent` (IntentEnum), `secondary_intents` (list[IntentEnum]), `confidence` (float 0.0–1.0), and `reasoning` (str) — not `raw_classification`.
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
- In local development (0 VND goal), both economy and premium tiers MUST map to local Ollama instances (e.g., distinguishing by parameter count or quantization level) to ensure full offline functionality.
- "Premium model" and "economy model" refer to LiteLLM model aliases defined in config, not specific vendor models.
- Streaming is delivered over the same in-process interface used for development/testing; Telegram webhook streaming integration is deferred to Week 6.
- The `SMALLTALK` intent routes directly to the economy model and bypasses RAG retrieval entirely (chitchat does not need product context).

---

## Out of Scope

- Telegram webhook integration (Week 6) — Week 6 will add a Telegram transport wrapper around `NodeStreamEvent`; `NodeStreamEvent` itself will not change. The wrapper will map `chat_id`/`message_id` for Telegram's edit-message-in-place streaming pattern. SSE (Server-Sent Events) is the Web/FastAPI streaming standard; Telegram uses Webhook POST, not SSE.
- Human-in-the-loop pause/resume (Week 4) — `interrupt_before=["answer_node"]` is the planned Week 4 target for HITL on Critical Actions (checkout, order confirmation). `escalation_node` is NOT the interrupt target; it is a pure-Python routing node with no side effects, so interrupting before it provides no user-visible control point. Week 3 prepares the checkpointer injection point; Week 4 adds the interrupt specification.
- Persistent conversation memory across restarts (Week 5)
- Load testing or rate limiting (Week 7)
- Any frontend or admin UI
