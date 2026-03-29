# Feature Specification: Async Persistence & Memory

**Feature Branch**: `005-async-persistence-memory`  
**Created**: 2026-03-11  
**Status**: Draft  
**Depends on**: `004-human-in-loop-hitl` (Week 4 — HITL system must be operational)

## Context

This feature gives the AI Sales Agent long-term memory. Without it, every conversation starts cold — the agent cannot recall a customer's budget stated last week, their pending order approved via HITL, or their repeated urgency signals. Week 5 closes that gap by persisting all conversation state to the database, building structured summaries, and enabling vector-based recall of past interactions alongside structured sales intent tracking.

The system already pauses for human approval (Week 4). Now it must also *remember* what happened before and after each approval.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Returning Customer Resumes Interrupted Conversation (Priority: P1)

A customer named Minh was chatting with the agent yesterday about a bulk order of industrial fans. He stated his budget was under 15 million VND and that he needed delivery within 3 days. Midway through, he had to leave. Today he reopens the chat. The agent greets him by name, recalls his stated budget and urgency, and continues from where the conversation left off — without making Minh repeat himself.

**Why this priority**: This is the most visible memory capability. A customer who has to re-explain their needs loses trust immediately. In SME sales, repeat customers are the primary revenue source.

**Independent Test**: Start a conversation session, provide budget and urgency, close the session, restart the server, reopen the session. The agent must recall budget and urgency without being told again.

**Acceptance Scenarios**:

1. **Given** a customer sent budget and urgency signals in a previous session, **When** the server restarts and the customer sends a new message in the same conversation thread, **Then** the agent's response references the stored budget and urgency without the customer re-stating them.
2. **Given** a HITL-approved order was recorded in a past session, **When** the customer asks "what was the order we confirmed last time?", **Then** the agent correctly retrieves and summarizes that order from memory.
3. **Given** the customer's last session ended mid-flow (e.g., agent was waiting for stock check), **When** the customer returns, **Then** the agent resumes at the exact pending step rather than starting over.

---

### User Story 2 — Sales Staff Sees Summarized Customer Intent Before Calling (Priority: P1)

A sales manager, Lan, reviews pending leads before calling them. She opens the admin dashboard and sees a customer profile that shows: "Budget: 20–30M VND, Urgency: HIGH (needs by Friday), Interest: air conditioning units × 3, Last contact: 2 days ago, Status: awaiting price quote." She calls the customer already knowing the context — no need to ask "so what are you looking for again?"

**Why this priority**: This directly converts AI conversations into sales-qualified leads. Without structured intent extraction and persistence, all the customer data collected by the agent is lost noise.

**Independent Test**: Conduct a multi-turn conversation where the customer states budget and urgency at different points. Then call the intent read API and verify the structured fields (budget, urgency, product interest, status) are correctly extracted and stored.

**Acceptance Scenarios**:

1. **Given** a customer conversation where budget is mentioned as "khoảng 25 triệu" in message 3 and urgency as "cần gấp trong tuần này" in message 7, **When** the intent extraction runs after the conversation, **Then** the intent record shows `budget_range: "20M–30M VND"`, `urgency: HIGH`, `deadline: "within this week"`.
2. **Given** a customer's intent status is `AWAITING_QUOTE`, **When** the sales staff marks the call as done and updates the status to `CONTACTED`, **Then** the intent tracking table reflects the new status and timestamp.
3. **Given** multiple customers with `urgency: HIGH`, **When** Lan queries the intent tracking API, **Then** results are sorted by urgency + recency so the hottest leads appear first.

---

### User Story 3 — Agent Recalls Long-term Context for Returning High-Value Customer (Priority: P2)

Thanh is a recurring B2B buyer who ordered refrigeration equipment 6 weeks ago. He returns today asking about expansion units. The agent — using semantic memory search — surfaces the fact that Thanh previously ordered a specific model (Model X-500), approved a HITL checkout for 3 units, and expressed a preference for same-day delivery. The agent proactively suggests compatible accessories for Model X-500 rather than starting a generic product search.

**Why this priority**: High-value repeat customers are the backbone of SME revenue. Proactive, context-aware responses convert significantly better than generic ones.

**Independent Test**: Insert a past conversation record with product and preference data into the memory store. Then send a new query semantically related to that history. Verify the retrieved memory is surfaced in the agent's context.

**Acceptance Scenarios**:

1. **Given** a conversation from 30+ days ago contains a HITL-approved order for "Model X-500 refrigeration unit × 3", **When** the customer asks "tôi muốn mua thêm thiết bị tương thích", **Then** the agent's retrieved context includes the prior order details.
2. **Given** 50 past conversation summaries are stored, **When** a semantic memory search is triggered with a new query, **Then** the top-3 most relevant past summaries are returned in under 500ms.
3. **Given** a customer's semantic memory contains a prior complaint about delivery delays, **When** the customer starts a new session, **Then** the agent's intent classifier flags COMPLAINT risk and escalates to a premium model per the Week 3 escalation policy.

---

### User Story 4 — System Survives Restart Without Losing Active HITL State (Priority: P1)

An order is pending HITL approval — the graph is paused, waiting for a human to approve a checkout for 5 industrial pumps worth 45M VND. The server crashes. When it restarts, the admin opens the `/review` endpoint, sees the same pending order with full state intact, and approves it. The customer's order is processed as if nothing happened.

**Why this priority**: This is data integrity. Losing a pending 45M VND order approval due to a restart is a catastrophic failure for an SME. Persistence of graph checkpoints is non-negotiable.

**Independent Test**: Trigger a HITL interrupt, then kill the server process. Restart the server. Call `/review` and verify the pending state with full context is still present and resumable.

**Acceptance Scenarios**:

1. **Given** a graph is paused at `interrupt_before=["order_node"]` with state containing order details, **When** the server process is killed and restarted, **Then** the `/review` endpoint returns the same pending state with identical order parameters.
2. **Given** the admin approves the resumed state post-restart, **When** the graph resumes, **Then** the order node executes exactly once with the approved parameters (idempotent).
3. **Given** a connection pool under load (10 concurrent conversations), **When** persistence writes happen simultaneously, **Then** all states are saved correctly with no race condition and connection count stays below 20.

---

### User Story 5 — Conversation Auto-Summarized After Long Thread (Priority: P2)

A customer has a 40-message conversation with the agent about comparing 5 products, asking detailed questions. Before the agent calls the LLM for the final recommendation, it auto-compresses the conversation history into a structured summary: key products discussed, final preference, stated budget, and unresolved questions. The LLM receives the compressed summary instead of all 40 raw messages — reducing token cost by at least 30%.

**Why this priority**: Without summarization, long conversations explode token costs. For an SME running on tight margins, an unbounded context window is a billing disaster.

**Independent Test**: Create a conversation with 30+ messages. Trigger summarization. Verify the summary contains structured fields (products, preferences, budget, open questions) and is shorter than the original by at least 30% in token count.

**Acceptance Scenarios**:

1. **Given** a conversation exceeds 20 messages, **When** the summarization task runs, **Then** a structured summary record is stored in the database with fields: `products_discussed`, `customer_preference`, `budget_stated`, `open_questions`, `summary_model`.
2. **Given** a summary exists for a conversation, **When** the agent retrieves context for the next turn, **Then** it uses the summary + recent N messages instead of the full raw history.
3. **Given** the summarization LLM call fails (model unavailable), **When** a new customer message arrives, **Then** the agent falls back to raw last-N-messages context and logs the summarization failure — it does not crash or block the customer response.

---

### Edge Cases

- **Cold start with no history**: A brand-new customer with zero prior conversations — agent must behave normally without any memory-retrieval step.
- **Corrupted checkpoint state**: If a persisted graph state is unparseable after a restart, the system must surface an error to the admin and not silently resume with bad data.
- **Intent extraction on vague messages**: Customer says "tôi muốn mua cái gì đó" (I want to buy something) — no budget, no urgency. Intent record must store `budget: null`, `urgency: UNKNOWN` rather than hallucinating values.
- **Duplicate session creation**: Two messages arrive simultaneously for the same `thread_id`. The system must use connection pooling correctly to prevent duplicate state writes.
- **Memory retrieval returns stale data**: A customer's intent changed between sessions (budget increased). The current session's stated values must always override older memory — recency wins.
- **Very long-term memory (100+ past conversations)**: Semantic search must still return in under 500ms and not retrieve irrelevant old context.
- **Conversation summary model unavailable**: Summarization must degrade gracefully — raw context fallback, no data loss.
- **Connection pool exhaustion**: If 20 connections are saturated, new requests must queue and return within a reasonable timeout rather than crashing.
- **Rapid message burst (stale intent write)**: A customer sends 2 messages within 1 second. Two background intent extraction tasks race to update the same IntentTracking record. The second write must detect the version conflict and retry with merged data — must not silently discard either message's signal.
- **Customer returns with a completely different project**: A customer who bought fans 3 months ago now asks about air conditioning. Semantic memory must not force-surface the fans context if relevance score is below threshold. The agent must treat this as a fresh inquiry.
- **Graph version mismatch on restart**: LangGraph code is updated (new node added) but old checkpoints remain in DB. Deserialization must fail visibly with a clear error, not silently resume with corrupted state.
- **Customer requests data deletion with a pending HITL order**: Admin must explicitly confirm before any checkpoint with a pending interrupt is deleted.

---

## Requirements *(mandatory)*

### Functional Requirements

**Persistence (5.1, 5.2)**

- **FR-001**: The system MUST persist all LangGraph conversation checkpoint states to the database such that a full server restart results in zero state loss for any active or pending conversation.
- **FR-001b**: Checkpoint storage MUST use a column format capable of holding large binary/structured payloads without truncation. Checkpoint records that exceed a configurable size threshold (default: 1MB) MUST trigger a warning log so operators are alerted before the database grows uncontrollably.
- **FR-001c**: The system MUST implement a checkpoint retention policy: checkpoints older than 90 days AND belonging to a fully resolved conversation (no pending HITL, status CONVERTED or LOST) MUST be eligible for automated cleanup. Cleanup MUST be auditable (log count of deleted records) and MUST NOT delete any checkpoint with a pending HITL interrupt.
- **FR-002**: The system MUST maintain a database connection pool with a hard ceiling of 20 concurrent connections. Requests beyond that limit MUST queue, not fail.
- **FR-003**: All persistence reads and writes MUST be non-blocking — they must not stall any concurrent conversation or API response.
- **FR-003b**: The four post-turn operations — (1) save checkpoint, (2) extract intent, (3) trigger summarization if threshold reached, (4) update semantic memory — MUST be dispatched as parallel async background tasks after the agent's response is returned to the customer. They MUST NOT block or delay the customer-facing response (Time To First Token). If any background task fails, it MUST log the failure and retry independently — it MUST NOT cause the agent's next response to fail.

**Structured Summarization (5.3)**

- **FR-004**: The system MUST auto-generate a structured conversation summary when a thread exceeds 20 messages, storing it as a structured record (not free text) with at minimum: products discussed, customer preference, budget stated, open questions, and the model used for summarization.
- **FR-005**: When a summary exists, the agent MUST use summary + last 5 raw messages as context, not the full raw history, for all subsequent LLM calls in that thread.
- **FR-006**: Summarization failures MUST be handled gracefully: the system falls back to raw-message context, logs the failure, and does not interrupt the customer experience.

**Semantic Memory (5.4)**

- **FR-007**: The system MUST store conversation summaries as vector embeddings to enable semantic retrieval across sessions and customers.
- **FR-008**: When a customer starts a new session, the system MUST perform a semantic search over their past conversation summaries and surface the top-3 most relevant to the current query context, subject to a minimum relevance score threshold (default: 0.75). Memory entries with a score below the threshold MUST be discarded — even if they are in the "top-3" — to prevent context drift (e.g., surfacing a 3-month-old complaint when a customer simply says "Hi"). Semantic memory search MUST filter strictly by `customer_id` — it MUST NOT surface memory entries belonging to a different customer under any circumstance.
- **FR-008b**: A single customer (`customer_id`) may have many conversation threads (`thread_id`) across time and platforms (e.g., one thread per project, one per platform). Memory scope is at the **customer** level: semantic memory searches across all `thread_id`s belonging to the same `customer_id`. However, to prevent context bleed between unrelated projects, semantic retrieval must rank by relevance score (FR-008 threshold) so that an old, unrelated thread does not contaminate the current conversation. Cross-platform identity merging (same customer on Telegram + Web) defaults to `merge=true`, operator-configurable.
- **FR-009**: Semantic memory retrieval MUST complete within 500ms (p95) under normal operating conditions. To meet this target, a vector similarity index (HNSW) MUST be created on the memory embedding column immediately at schema creation time — not deferred. Index parameters (e.g., `m`, `ef_construction`) MUST be tuned for the expected dataset size and documented in the ADR.
- **FR-010**: Each memory embedding record MUST store: `embedding_model`, `model_version`, `dimension`, `thread_id`, `customer_id`, `created_at`. Embeddings from different model versions MUST NOT be mixed in search.
- **FR-010b**: When the embedding model is changed (e.g., upgrading or switching providers), all existing embeddings generated by the old model version MUST be flagged as `status: STALE` and excluded from semantic search until they are re-embedded by the new model. The system MUST detect a model version mismatch at search time and emit an operator alert. A re-embedding migration procedure (re-compute embeddings for all STALE records) MUST be supported as an offline administrative operation. There MUST be no period where stale and fresh embeddings are mixed in the same search query.

**Sales Intent Extraction (5.5)**

- **FR-011**: After each conversation turn, the system MUST evaluate the turn's primary intent classification before deciding whether to run intent extraction. Intent extraction MUST be skipped when the classified intent is `FOLLOW_UP`, `OTHER`, or `SMALLTALK` (e.g., messages like "Ok", "Cảm ơn", "Xin chào", "Được rồi"). Extraction MUST only run when the turn contains a signal-bearing intent (e.g., `PRODUCT_INQUIRY`, `PRICE_QUERY`, `COMPLAINT`, `NEGOTIATION`, `PURCHASE_INTENT`). This prevents token waste on low-signal turns.
- **FR-011b**: When intent extraction does run, it MUST extract and update structured fields: `budget_range`, `urgency_level` (LOW / MEDIUM / HIGH / UNKNOWN), `product_interest`, `decision_timeline`, and `contact_preference`.
- **FR-013**: If a field cannot be determined from the conversation, it MUST be stored as `null` / `UNKNOWN` — the system MUST NOT hallucinate values.
- **FR-014**: When a customer explicitly restates intent in a new session (e.g., new budget), the current session's stated values MUST override the stored values (recency wins).

**Intent Tracking Table (5.6)**

- **FR-015**: The system MUST maintain an intent tracking record per customer with fields: `customer_id`, `thread_id`, `intent_status` (NEW / ENGAGED / AWAITING_QUOTE / CONTACTED / CONVERTED / LOST), `last_updated`, `version` (integer, starts at 1), all extracted intent fields.
- **FR-015b**: Intent tracking writes MUST use optimistic locking via the `version` field: any update MUST include a `WHERE version = :expected_version` clause and increment `version` on success. If two background tasks attempt to update the same record concurrently (e.g., two rapid customer messages trigger two intent extractions), the second write MUST detect the version mismatch, re-read the current record, merge or discard its update, and retry — it MUST NOT silently overwrite the first update with stale data.
- **FR-016**: Intent status transitions MUST be explicit and auditable — every status change MUST be recorded with a timestamp and the triggering event (agent action, admin action, or system rule).
- **FR-017**: The admin MUST be able to query intent records filtered by `urgency_level` and `intent_status`, ordered by recency.
- **FR-018**: The system MUST handle checkpoint deserialization failures caused by LangGraph graph schema changes (e.g., a node added or renamed after a checkpoint was written). When deserialization fails, the system MUST: (1) log the error with `checkpoint_id`, `thread_id`, and detected `graph_version` mismatch, (2) surface the failed checkpoint to the admin as `status: INCOMPATIBLE` via the `/review` endpoint, and (3) NOT silently crash or resume with corrupted state. A manual migration or discard path must be available to the operator.
- **FR-019**: The system MUST provide a customer data deletion capability. When triggered for a given `customer_id`, it MUST permanently delete: all semantic memory entries, all intent tracking records, all conversation summaries, and all checkpoints for that customer. Checkpoints with pending HITL interrupts MUST require explicit admin confirmation before deletion. The deletion operation MUST be auditable (logged with operator ID, timestamp, and count of deleted records).

### Key Entities

- **ConversationCheckpoint**: Serialized LangGraph graph state for a given `thread_id` and `checkpoint_id`. Enables resume-after-restart. Contains full node states, pending interrupts, and metadata. **Storage risk**: payload size can grow large with long histories — storage format must handle large structured payloads without truncation. Cleanup policy (FR-001c) prevents unbounded growth.
- **ConversationSummary**: Structured, compressed representation of a conversation thread. Contains `products_discussed` (list), `customer_preference` (text), `budget_stated` (text), `open_questions` (list), `turn_count_at_summary`, `summary_model`, `embedding` (vector).
- **SalesIntent**: Structured record of what the customer wants. Fields: `budget_range`, `urgency_level`, `product_interest`, `decision_timeline`, `contact_preference`. Updated only on signal-bearing turns (not FOLLOW_UP/OTHER).
- **IntentTracking**: Customer-level CRM view aggregating intent + status. Fields: `customer_id`, `thread_id`, `intent_status`, `last_updated`, `version` (optimistic lock counter), all SalesIntent fields. One record per customer (upserted). Concurrent writes are guarded by optimistic locking (FR-015b) to prevent stale overwrites from rapid message bursts.
- **SemanticMemoryEntry**: Vector embedding of a conversation summary, linked to both `thread_id` (session scope) and `customer_id` (cross-session scope). Queries MUST always filter by `customer_id` to prevent cross-customer memory contamination. HNSW index is mandatory at creation time.

---

## Known Risks & Mitigations

| # | Risk | Impact | Mitigation in Spec |
|---|------|--------|--------------------|
| R1 | **Checkpoint Size Explosion** — LangGraph state with long message history serializes to large payloads, filling the database over time. | HIGH | FR-001b (size warning) + FR-001c (90-day retention cleanup, never deletes pending HITL). |
| R2 | **Semantic Search Latency > 500ms on weak hardware** — Vector search without an index will miss SC-005 on local/dev machines. | MEDIUM | FR-009: HNSW index mandatory at schema creation. Parameters documented in ADR. |
| R3 | **Cross-platform identity ambiguity** — Same customer on Telegram + Web creates 2 thread_ids. Without clear scoping, memory may split or incorrectly merge. | HIGH | FR-008b: memory scoped by `customer_id`. One customer can have many `thread_id`s. Cross-platform merge defaults `true`, operator-configurable. |
| R4 | **Token waste on low-signal turns** — Intent extraction on "Ok" / "Cảm ơn" burns cost with zero value. | MEDIUM | FR-011: extraction gated on signal-bearing intent classes only. |
| R5 | **Memory Leakage (cross-customer contamination)** — Semantic search without strict `customer_id` filter exposes another customer's data. | CRITICAL | FR-008: strict `customer_id` filter on every semantic query. Non-negotiable. |
| R6 | **Stale Data / Race Condition in Intent Extraction** — Two rapid messages trigger two concurrent background intent writes to the same record. The second write overwrites the first with old data. | HIGH | FR-015b: optimistic locking via `version` field. Any update must check `version`, increment on success, re-read and retry on mismatch. |
| R7 | **Context Drift in Semantic Memory** — "Top-3 most relevant" without a score floor surfaces stale/unrelated old threads (e.g., a complaint from 3 months ago surfaced on "Hi"). | MEDIUM | FR-008: minimum relevance score threshold (default 0.75). Entries below threshold discarded even if ranked top-3. |
| R8 | **Embedding Model Drift** — Switching embedding providers invalidates all stored embeddings silently, producing nonsensical similarity scores. | HIGH | FR-010b: old embeddings flagged `STALE` and excluded from search. Mismatch alert to operator. Offline re-embedding migration procedure required. |
| R9 | **Graph Version Mismatch** — LangGraph code updates (new/renamed node) break deserialization of old checkpoints, causing silent crashes or corrupt state resume. | HIGH | FR-018: deserialization failures surface as `INCOMPATIBLE` status on `/review`. Never silent. Operator migration/discard path required. |
| R10 | **Connection Pool Bottleneck** — 4 concurrent post-turn operations (checkpoint + intent + summarize + vector) on a 20-connection pool can queue-starve under 15+ simultaneous users. | MEDIUM | FR-003b: all 4 operations dispatched as parallel background tasks AFTER response is returned. Customer TTFT is decoupled from persistence latency. |
| R11 | **Right to be Forgotten** — No deletion path for customer data violates privacy expectations and may conflict with SME legal obligations. | MEDIUM | FR-019: full customer data deletion API with audit log. Pending HITL checkpoints require explicit admin confirmation before deletion. |

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A customer returning after a server restart can continue their conversation without re-stating any previously provided information — verified in 100% of restart tests.
- **SC-002**: The database connection pool never exceeds 20 connections under a load of 15 simultaneous conversations — verified by connection count monitoring.
- **SC-003**: Conversation summaries reduce the token count sent to the LLM by at least 30% compared to sending full raw history for threads with 20+ messages.
- **SC-004**: Semantic memory retrieval surfaces at least 1 relevant past conversation for returning customers in 90% of test cases where relevant history exists.
- **SC-005**: Semantic memory queries return results in under 500ms at the 95th percentile for a dataset of 500+ stored summaries.
- **SC-006**: Sales intent extraction correctly captures `budget_range` and `urgency_level` in at least 85% of test conversations where those values are explicitly stated.
- **SC-007**: Intent status transitions are 100% auditable — every change has a timestamp and triggering event, with zero silent updates.
- **SC-008**: A HITL-pending graph state is fully recoverable after a server restart with zero manual intervention required to restore it to the `/review` endpoint.
- **SC-009**: The customer-facing response time (Time To First Token) is not increased by more than 50ms compared to a baseline without persistence — all post-turn storage operations run as parallel background tasks decoupled from the response path.
- **SC-010**: Under a load of 15 concurrent conversations each sending 2 rapid messages, zero IntentTracking records contain stale/overwritten data — verified by checking `version` monotonically increases with no skipped updates.

---

## Assumptions

- The Week 4 HITL system (`004-human-in-loop-hitl`) is fully operational. `interrupt_before`, `get_state()`, `update_state()`, and the `/review` API are all working.
- `thread_id` is the session-level correlation key for checkpoints (one conversation = one thread, scoped to one platform session).
- `customer_id` is the cross-session, cross-platform identifier (e.g., Telegram user ID, Web user ID). Semantic memory and intent tracking are scoped by `customer_id`, not `thread_id`. These two identifiers serve different purposes and must not be conflated.
- Cross-platform memory merging (same customer contacting from Telegram + Web) is enabled by default: both threads map to the same `customer_id` and share semantic memory. This behavior can be disabled per deployment via configuration.
- Summarization is triggered reactively (after the 20th message in a thread) rather than on a scheduled cron job, to stay consistent with the event-driven architecture.
- The embedding model used in Week 5 is the same one established in Week 2. Dimension and model name are governance-controlled — no mixing allowed.
- Connection pool ceiling of 20 is derived from the SME deployment constraint: single Postgres instance on modest hardware; more than 20 active connections risks memory pressure.
- Intent extraction runs only on signal-bearing turns (PRODUCT_INQUIRY, PRICE_QUERY, COMPLAINT, NEGOTIATION, PURCHASE_INTENT) as a background task. FOLLOW_UP and OTHER turns are skipped to avoid token waste.
- Data retention for conversation checkpoints follows 90-day default with automated cleanup for resolved conversations (FR-001c); summaries and intent records are retained indefinitely unless explicitly deleted.
