# Feature Specification: Human-in-the-Loop (HITL) Control System

**Feature Branch**: `004-human-in-loop-hitl`  
**Created**: 2026-03-05  
**Status**: Draft  

---

## Clarifications

### Session 2026-03-06 (Round 1 — Basic Workflows)

- Q: Customer messaging during HITL pause → A: Queue new messages + send auto-reply ("Your order is being reviewed, est. 2 min"). Process queued messages sequentially after admin decision.
- Q: Request-edit flow destination → A: State remains paused at same node; admin must explicitly approve to resume. No auto-resumption after edit.
- Q: Cost threshold basis → A: Estimate based on per-operation context (compressed form), not full conversation history.
- Q: Admin state edit validation → A: Type-only validation at endpoint level (e.g., price must be number). Semantic rules (price > 0) validated at graph resumption.
- Q: Duplicate order prevention → A: Optimistic locking via version field on InterruptedSession. Second approval with stale version rejected with conflict error.

### Session 2026-03-06 (Round 2 — Deep Design Risks)

- Q: State persistence (double persistence risk) → A: Use LangGraph PostgresSaver only for full state. Lightweight HITLMetadata table stores pause reason, admin_id, approval status, escalation_count. Single source of truth prevents sync bugs.
- Q: History consistency after edit (AI re-reads old context) → A: When admin edits state, append synthetic system message to conversation_history: "SYSTEM [Admin override]: [field] corrected from [old] to [new]." LLM explicitly sees correction.
- Q: Cascading HITL (infinite loop risk) → A: Max 2 HITL cycles per order. Add escalation_count to InterruptedSession (incremented on each pause). On 3rd trigger, force rejection or escalate to human support (Week 6).
- Q: On-timeout escalation (admin forgets to review) → A: After 30 min without review: send Telegram to customer "Review in progress, will respond shortly." After 60 min: escalate to human support queue (handoff to human agent, not auto-reject).
- Q: Dry-run validation (admin deletes required fields) → A: Full Pydantic schema validation at /review endpoint before accepting edit. Validate against downstream node's TypedDict (required field presence + type). Reject if missing required fields.

### Session 2026-03-06 (Round 3 — Implementation Complexity)

- Q: QueuedMessage processing architecture → A: Post-approval node consumes queue explicitly. After admin approves and graph resumes at order_node, first step: AI reads QueuedMessage table, merges follow-ups as "Customer followed up:" messages, THEN proceeds with order execution.
- Q: QueuedMessage cleanup strategy → A: Soft delete with 90-day archive. Mark processed messages (processed=true) in DB. Auto-archive old entries after 90 days. Retain for audit/support investigation (customer service can review "Why did they say X?").
- Q: Human support queue definition → A: Postgres SupportQueue table (session_id, reason, created_at, assigned_to, status). Source of truth for escalations. Week 6 builds UI/Telegram integration. Week 4 focuses on populating this table (not the UI).
- Q: Rejection flow & customer communication → A: Route to customer_support_node, NOT immediate __end__. Node composes empathetic response explaining reason and offering support contact. Then __end__. Customer feels acknowledged, not abandoned.
- Q: Synthetic message placement (strength/positioning) → A: Inject after customer's last message (before any previous AI response), not at tail. Positions correction in narrative flow. LLM sees it as present-day context, not tail annotation. Prevents contradiction with older messages above it.

---

## Overview

The HITL (Human-in-the-Loop) system is a **business-critical risk control mechanism** that pauses the AI agent's execution before performing sensitive, revenue-affecting, or legally-significant actions. An admin reviews the AI's **intended next action**, can modify the proposed state, and explicitly approve/reject before execution resumes. This ensures the AI cannot autonomously cause financial harm or compliance violations.

**Core Principle**: HITL is placed **inside the LangGraph state machine** (not patched into FastAPI controllers), and activates **before side-effects occur** — not after damage is done.

---

## User Scenarios & Testing

### User Story 1 - Admin Reviews & Approves Order (Priority: P1)

A customer requests to place an order for 10 units of Product A. The AI agent collects the order details (product, quantity, customer info) and reaches the `order_node`. The graph pauses and flags the order for human review. The admin opens the `/review` endpoint, sees the full order state (items, total cost, customer confirmation status), verifies correctness, and clicks "Approve". The graph resumes and executes the order placement.

**Why this priority**: Ordering is the primary revenue-generating action; approving orders is the #1 HITL use case for SMEs. Implementing this unlocks the entire HITL system.

**Independent Test**: Can be fully tested by constructing a sample order, halting at `order_node`, fetching state, approving via `/review`, and confirming the order executes. Delivers clear value: zero unreviewed orders.

**Acceptance Scenarios**:

1. **Given** the AI has collected valid order details, **When** the graph reaches `order_node`, **Then** execution pauses and an interruption flag is set in the state
2. **Given** an interruption is active, **When** admin calls `GET /graph/{session_id}/state`, **Then** they receive the current state plus the name of the next node to be executed
3. **Given** the current state is displayed, **When** admin approves via `POST /review` with action "approve", **Then** the graph resumes from `order_node` and order is persisted
4. **Given** a paused order, **When** admin modifies the proposed order total and submits approval, **Then** the state is updated and the modified order is persisted
5. **Given** the graph is paused awaiting admin review, **When** the customer sends additional messages via Telegram, **Then** those messages are queued and the customer receives an auto-reply: "Your order is being reviewed (est. 2 min). We'll respond shortly." Messages are processed sequentially after admin approval/rejection.

---

### User Story 2 - Admin Rejects Order & Provides Reason (Priority: P1)

The AI has prepared an order, but the admin notices the customer's credit limit has been exceeded. The admin rejects the order via the `/review` endpoint and includes a rejection reason ("Customer credit limit exceeded"). The graph pauses execution, the state is updated with the rejection reason, and the AI resumes with instructions to inform the customer why the order was declined.

**Why this priority**: Rejection is as critical as approval—the system must handle denials gracefully without customer confusion.

**Independent Test**: Can be tested by constructing an order, rejecting it with a reason, and verifying the AI's next response to the customer reflects that reason.

**Acceptance Scenarios**:

1. **Given** an order is paused at `order_node`, **When** admin calls `POST /review` with action "reject" and a reason, **Then** the state is updated with the rejection metadata
2. **Given** the order is rejected, **When** the graph resumes, **Then** the AI's next node produces a customer-facing response explaining the rejection reason
3. **Given** rejection reason is stored, **When** admin or AI queries the session history, **Then** the rejection reason is logged and retrievable

---

### User Story 3 - Admin Requests Edit & Re-Review (Priority: P1)

The AI proposes a final price for a customized product order (e.g., 1,200,000 VND), but the admin notices a calculation error. The admin requests an edit, modifies the price to the correct amount (1,100,000 VND), and resubmits for approval. The AI resumes with the corrected price and proceeds to place the order.

**Why this priority**: Edit capability prevents human admins from having to reject and restart orders; it streamlines the approval workflow.

**Independent Test**: Can be tested by pausing an order, requesting an edit, modifying state, resubmitting, and verifying the updated state persists through order placement.

**Acceptance Scenarios**:

1. **Given** a paused order state, **When** admin calls `POST /review` with action "request_edit" and edits to the state (e.g., price field), **Then** the state is updated with the new values
2. **Given** state is edited, **When** admin provides explicit approval (action "approve") after edit, **Then** the graph resumes with the modified state from the same interrupted node (no rewind or auto-resumption)
3. **Given** edits are applied, **When** order is persisted, **Then** the final order reflects the admin-corrected values, not the original AI proposal
4. **Given** admin edits state with invalid field type (e.g., price as string instead of number), **When** `/review` validates the request, **Then** endpoint returns validation error immediately without modifying state; admin must fix and resubmit

---

### User Story 4 - Confidence Guard Triggers HITL Automatically (Priority: P2)

The AI searches for product information but retrieves results with low confidence (similarity score: 0.65, below the 0.7 threshold). Instead of answering with low-confidence data, the system automatically escalates to human review. An admin reviews the query, retrieves the correct information manually, and provides it to the customer.

**Why this priority**: Prevents hallucinations; protects customer trust. Confidence guards are a safety mechanism, not optional.

**Independent Test**: Can be tested by submitting a query that yields low-confidence results, verifying automatic HITL escalation occurs, and confirming no low-confidence answer is sent to the customer.

**Acceptance Scenarios**:

1. **Given** the AI retrieves a response with confidence < 0.7, **When** the response is prepared, **Then** execution is automatically interrupted and flagged for human review
2. **Given** a low-confidence escalation, **When** admin accesses the review page, **Then** they see the query, retrieved data, confidence score, and can manually provide a correct answer
3. **Given** admin provides a manual answer, **When** the graph resumes, **Then** the customer receives the admin-provided answer with explicit marking ("Verified by support team")

---

### User Story 5 - Cost Guard Triggers HITL for Expensive Operations (Priority: P2)

The AI is about to call an expensive LLM model or perform a complex operation estimated to cost 10,000 tokens, exceeding the SME's per-operation budget threshold (8,000 tokens). The system escalates for human approval before incurring the cost. The admin reviews the operation, decides if it's worth the cost, and approves or requests an alternative approach.

**Why this priority**: SMEs are extremely cost-sensitive. Cost guards prevent runaway expenses and show engineering maturity (cost-aware AI).

**Independent Test**: Can be tested by simulating an operation that would exceed the cost threshold, verifying escalation occurs, and confirming the operation is blocked until approved.

**Acceptance Scenarios**:

1. **Given** an operation is estimated to cost > threshold tokens, **When** the graph reaches the decision node, **Then** execution is interrupted for cost approval
2. **Given** a cost escalation, **When** admin reviews via `/review`, **Then** they see the operation description and estimated cost (calculated from compressed per-operation context, not full conversation history)
3. **Given** admin rejects due to cost, **When** the graph resumes, **Then** an alternative (cheaper) operation path is attempted or the user is offered a simpler solution

---

### Edge Cases

- **Concurrent approval attempts (double-click due to lag)**: If an admin clicks the approve button twice in quick succession, the InterruptedSession maintains a `version` field (optimistic locking). The first approval increments the version and succeeds; the second approval with the stale version is rejected with a clear conflict error: "This order was already approved by another admin."
- **Admin approval times out**: Timeout preserves the paused state; resumption requires re-approval if timeout exceeds [NEEDS CLARIFICATION: timeout duration not specified, suggested default 30 minutes].
- **Admin edits state with invalid data**: Type-only validation occurs at `/review` endpoint level (e.g., price must be number). Validation error returned immediately; state unchanged. Semantic validation (e.g., price > 0) deferred to graph resumption; if invalid, graph pauses again with error for admin to fix.
- **Zombie sessions (unapproved orders hang indefinitely)**: Paused sessions are marked as "abandoned" after [NEEDS CLARIFICATION: abandonment threshold time not specified, suggested default 24 hours]. Admin dashboard shows abandoned sessions; admin can manually resume, close, or escalate them.
- **Customer messages during HITL**: New messages are queued and customer receives auto-reply: "Your order is being reviewed (est. 2 min). We'll respond shortly." Queued messages processed sequentially after admin approval/rejection to maintain conversation coherence.
- **Missing confidence score**: Treat missing score as 0.0 and escalate conservatively to HITL.

## Requirements

### Functional Requirements

- **FR-001**: LangGraph must support `interrupt_before=["order_node", "checkout_node", "refund_node", "pricing_node"]` to pause execution before sensitive nodes
- **FR-002**: System MUST provide `graph.get_state()` endpoint to fetch the current state and the next node name without exposing internal logs
- **FR-003**: `/review` API endpoint MUST support three actions: "approve", "reject", "request_edit" with idempotent behavior (safe to call multiple times)
- **FR-004**: Admin MUST be able to edit specific fields in the paused state via `/review` with `request_edit` action; changes MUST pass full Pydantic schema validation (required field presence + type checking); changes MUST be persisted
- **FR-005**: Confidence scores (similarity, rerank, or model-provided) MUST be calculated explicitly and stored in the state; if confidence < 0.7, the system MUST automatically escalate to HITL
- **FR-006**: Cost estimates MUST be calculated before executing expensive operations using per-operation compressed context; if estimated cost > threshold, execution MUST pause for approval
- **FR-007**: All state edits and approvals MUST be logged with timestamp, admin user ID, and change description for audit trails
- **FR-008**: A resumed graph MUST use the updated/approved state, not recompute from original input; graph remains paused at the same node after edit until explicit approval is submitted
- **FR-009**: Rejection MUST include a reason field that is stored in state and accessible to the AI for customer-facing responses
- **FR-010**: The system MUST distinguish between "paused for review" and "rejected" states to avoid re-execution of rejected orders; use HITLMetadata.status field
- **FR-011**: InterruptedSession MUST include a `version` field for optimistic locking; concurrent approvals are rejected if version is stale; only the first approval succeeds
- **FR-012**: When graph is paused awaiting approval, new customer messages MUST be queued and customer MUST receive auto-reply within 2 seconds. Queued messages processed sequentially after admin decision to maintain conversation coherence
- **FR-013**: Full graph state retrieved from LangGraph PostgresSaver (checkpointer), not from custom InterruptedSession table; HITLMetadata stores pause metadata only (reason, admin_id, timestamps, escalation_count)
- **FR-014**: When admin edits state, a synthetic system message MUST be appended to conversation_history: "SYSTEM [Admin override]: [field] updated from [old_value] to [new_value]." This ensures LLM sees the correction explicitly
- **FR-015**: HITL escalation_count MUST be tracked per order; maximum 2 HITL pauses allowed; on 3rd escalation trigger, order MUST be rejected or escalated to human support (no additional automatic pausing)
- **FR-016**: If order is not reviewed within 30 minutes of pause, customer MUST be notified via Telegram: "Your order is being reviewed. We'll respond shortly." If not reviewed within 60 minutes, order MUST be escalated to human support queue (hand off to support team, not auto-rejected)

- **FR-017**: After admin approves (or edits + approves), graph MUST resume at order_node. First action MUST be: read all processed=false QueuedMessages for this session in timestamp order. Merge customer follow-ups into state as "Customer followed up: [message text]" messages BEFORE proceeding with order execution. Ensures AI explicitly sees follow-ups and doesn't ignore 30-min communication gap.

- **FR-018**: Rejection flow MUST NOT immediately end graph (__end__). MUST route to customer_support_node that composes empathetic message: "We're unable to process your order because [reason]. Our support team will help. Contact: [support link]." THEN end. Prevents harsh abrupt termination.

- **FR-019**: SupportQueue table MUST be populated on every escalation (timeout_60min, max_hitl_exceeded, admin rejects order, low-confidence refund). Schema: session_id, reason, created_at, assigned_to (nullable), context_snapshot (JSON order details), status (pending/assigned/resolved/closed). Week 6 builds UI; Week 4 focus is populating this table.

- **FR-020**: Synthetic messages (admin edits) MUST be inserted after the customer's last message, not at tail of history. Positions correction in conversation narrative flow so LLM treats it as present-day context. Prevents older contradictory messages from outweighing the admin's explicit correction.

- **FR-021**: QueuedMessage cleanup policy: Mark processed=true immediately after consumption. Auto-archive (set archived=true) after 90-day retention window. Archived messages are read-only (support team can query for audit). No hard delete. Supports customer service investigation and compliance audits.

### Key Entities

- **InterruptedSession**: Represents a paused graph execution (HITL metadata only; full state stored in LangGraph PostgresSaver)
  - `session_id`: Unique identifier
  - `next_node`: Name of the node about to execute
  - `reason`: Why execution was interrupted ("order_approval", "low_confidence", "cost_limit", "refund_approval")
  - `timestamp`: When interruption occurred
  - `admin_id`: ID of the admin reviewing (nullable until assigned)
  - `version`: Integer version number for optimistic locking (conflict prevention)
  - `escalation_count`: Int (0–2); incremented each time HITL pauses for this order; 3rd pause forces rejection/escalation

- **HITLMetadata**: Lightweight table for pause/approval tracking
  - `session_id`: Which conversation
  - `pause_id`: Unique pause instance ID (for tracking multiple pauses per session)
  - `pause_reason`: User-friendly description
  - `paused_at`: Timestamp of pause
  - `timeout_notified_at`: Timestamp of 30-min customer notification (nullable)
  - `escalated_to_support_at`: Timestamp of escalation to human support (nullable)
  - `status`: "paused" | "approved" | "rejected" | "escalated" | "abandoned"

- **ReviewAction**: Represents an admin decision on a paused session
  - `session_id`: Which session is being reviewed
  - `pause_id`: Which pause instance
  - `action`: "approve" | "reject" | "request_edit"
  - `state_edits`: JSON diff of changes (nullable if no edits)
  - `reason_or_comment`: Text explanation from admin
  - `timestamp`: When decision was made
  - `admin_user_id`: Who made the decision
  - `expected_version`: Version expected at approval; fails if stale (prevents double-click duplicates)

- **ConfidenceScore**: Metadata for answer confidence
  - `session_id`: Which conversation
  - `turn_id`: Which AI turn
  - `similarity_score`: Vector retrieval confidence (0.0–1.0)
  - `rerank_score`: Rerank model score (optional, 0.0–1.0)
  - `model_confidence`: LLM-provided confidence (optional)
  - `overall_confidence`: Combined score (0.0–1.0)
  - `escalation_triggered`: Boolean (true if confidence < 0.7)

- **QueuedMessage**: Customer messages received during HITL pause
  - `session_id`: Which conversation
  - `message_text`: Customer's message content
  - `received_at`: Timestamp when message arrived
  - `processed`: Boolean (false until graph resumes and processes sequentially)
  - `archived`: Boolean (true after 90-day retention window; marked for cleanup)

- **SupportQueue**: Escalated orders awaiting human support (NEW in Round 3)
  - `session_id`: Which conversation
  - `reason`: Why escalated ("timeout_60min", "rejected_order", "max_hitl_exceeded")
  - `created_at`: Timestamp of escalation
  - `assigned_to`: Support agent ID (nullable until assigned)
  - `context_snapshot`: JSON of order details + customer info for support agent
  - `status`: "pending" | "assigned" | "resolved" | "closed"

## Success Criteria

### Measurable Outcomes

- **SC-001**: All order placements MUST be approved by a human before persistence; 0% of unapproved orders should reach the database
- **SC-002**: Admin MUST be able to review a paused order and make a decision (approve/reject/edit) within 60 seconds of retrieval (latency of `/review` < 200ms)
- **SC-003**: 100% of low-confidence responses (confidence < 0.7) MUST be escalated to HITL; zero low-confidence answers should reach the customer without human verification
- **SC-004**: Cost guards MUST activate on 100% of operations exceeding the cost threshold (threshold calculated from compressed per-operation context); no unexpected high-cost operations should execute without approval
- **SC-005**: Admin edits to paused state MUST be persisted and used for the resumed order; zero orders should use pre-edit values after approval
- **SC-006**: All approvals, rejections, and edits MUST be logged in audit trail with full context; compliance teams should be able to retrieve approval history for any order in < 1 second
- **SC-007**: System MUST gracefully handle concurrent approval attempts via optimistic locking; exactly one approval should succeed with version increment, others should fail with conflict error
- **SC-008**: Paused sessions MUST remain paused unless explicitly resumed, rejected, or escalated after 60 minutes of no review; zero spontaneous resumptions should occur
- **SC-009**: Rejection reason MUST be available to the AI for customer-facing responses; the customer should receive a clear, personalized explanation of why their order/request was declined
- **SC-010**: The system MUST maintain a transparent separation between "paused for review", "rejected", and "escalated"; admins and the AI should always know which state an order is in (tracked via HITLMetadata.status)
- **SC-011**: When graph is paused, customer MUST receive auto-reply within 2 seconds confirming order is under review; queued messages MUST be processed sequentially and completely after admin decision without loss
- **SC-012**: Admin state edits MUST be validated for type AND required field presence (full Pydantic schema) at endpoint level before acceptance; validation errors returned immediately without state modification
- **SC-013**: On pause, synthetic system message MUST be appended to conversation history when edits made (e.g., "SYSTEM [Admin override]: price updated from 1.2M to 1.1M"). 100% of resumed graphs MUST see this message to prevent history inconsistency.
- **SC-014**: Maximum 2 HITL pauses allowed per order (escalation_count ≤ 2). On 3rd escalation, order MUST be rejected or escalated to human support queue (not paused again). Zero infinite loops.
- **SC-015**: On timeout (30 min no review), customer receives Telegram notification. On 60 min no review, order escalated to human support queue with full context. Zero "zombie" orders left in paused state indefinitely.
- **SC-016**: Full graph state persisted in LangGraph PostgresSaver only; HITLMetadata stores metadata only (pause reason, admin_id, timestamps, escalation_count, status). Single source of truth for state (LangGraph); no duplication.

- **SC-017**: When graph resumes after approval, QueuedMessage consumption is first step (before order logic). All follow-up messages are explicitly merged into conversation_history with "Customer followed up: [text]" prefix. AI's final response acknowledges new context.

- **SC-018**: Rejection always routes to customer_support_node. Node retrieves order_id, reason, support_contact. Sends message to customer: "We're unable to process order [order_id] because [reason]. Support team will help at [link]. Thank you!" Then transitions to __end__. Customer receives closure, not silence.

- **SC-019**: Every escalation writes to SupportQueue table. Timeout escalation: reason="timeout_60min", context_snapshot includes order details + last 3 customer messages. Rejection escalation: reason="rejected_order", includes rejection_reason field. SupportQueue.status initially "pending" until Week 6 agent assigns.

- **SC-020**: Synthetic messages are positioned *immediately after customer's last message*, before any previous AI responses. JSON structure: {type: "system_override", timestamp: [admin_edit_time], field: [field_name], old_value: [old], new_value: [new]}. LLM processes this as present-day correction in conversation flow.

- **SC-021**: Query processing QueuedMessages by session_id, order by received_at ASC. Process exactly 1 timeout notification per session_id (idempotent). After 60-min timeout, insert exactly 1 SupportQueue record (idempotent on session_id). Archived=true QueuedMessages excluded from active processing. Archive job runs nightly; archived messages remain queryable for 30 days.

---

## Assumptions

1. **LangGraph Capabilities**: The team has validated that LangGraph v0.1+ supports `interrupt_before` and `get_state()` methods as documented
2. **Admin Authentication**: Admin users are authenticated via existing auth system (credentials validated before `/review` access)
3. **Confidence Calculation**: Confidence scores are already computed in Week 2–3 (vector similarity, rerank scores, or model-provided); this feature consumes those scores
4. **Cost Estimation**: Cost estimation logic exists (token counting, per-model pricing); this feature uses pre-computed cost estimates
5. **Async FastAPI**: The `/review` endpoint is async and non-blocking (uses `httpx` for any external calls, no sync I/O)
6. **Single Database**: All state, logs, and audit trails are persisted in PostgreSQL only
7. **Session Management**: Conversation sessions are already tracked; `session_id` uniquely identifies a conversation
8. **State Serialization**: Typed state (TypedDict) is fully JSON-serializable for storage and retrieval

---

## Out of Scope (Explicitly Excluded)

- **Multi-level approvals** (e.g., manager must approve after admin approval) — scope is single-level approval
- **Approval templates or approval workflows** — the system supports flexible ad-hoc edits, not pre-defined workflow rules
- **Scheduled auto-approval** — orders will not auto-approve after a timeout; human decision is always required
- **UI dashboard** — this feature provides the `/review` API; UI/frontend is out of scope (Week 6+)
- **Telegram integration** — HITL is a backend system; Telegram webhook integration is Week 6
- **Mobile app approvals** — approvals are via REST API; any mobile client is out of scope
