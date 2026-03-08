# Feature Specification: Human-in-the-Loop (HITL) Control System

**Feature Branch**: `004-human-in-loop-hitl`  
**Created**: 2026-03-05  
**Status**: Draft  

---

## Clarifications

### Session 2026-03-06 (Round 1 — Basic Workflows)

- Q: Customer messaging during HITL pause → A: The **Paused Session Gateway** (webhook handler) intercepts all messages when a session is paused; new messages are queued and auto-reply sent within 2s. The graph is never invoked from a paused session via Telegram input.
- Q: Request-edit flow destination → A: State remains paused at same node; admin must explicitly approve to resume. No auto-resumption after edit.
- Q: Cost threshold basis → A: Estimate based on per-operation context (compressed form), not full conversation history.
- Q: Admin state edit validation → A: Full Pydantic schema validation at endpoint level (required fields + type). Semantic rules (price > 0) validated at graph resumption.
- Q: Duplicate order prevention → A: Optimistic locking via version field on InterruptedSession. Second approval with stale version rejected with conflict error.

### Session 2026-03-06 (Round 2 — Deep Design Risks)

- Q: State persistence (double persistence risk) → A: Use LangGraph PostgresSaver only for full state. Lightweight HITLMetadata table stores pause reason, admin_id, approval status, escalation_count. Single source of truth prevents sync bugs.
- Q: History consistency after edit (AI re-reads old context) → A: When admin edits state, append synthetic system message to conversation_history: "SYSTEM [Admin override]: [field] corrected from [old] to [new]." LLM explicitly sees correction.
- Q: Cascading HITL (infinite loop risk) → A: Max 2 HITL cycles per order. Add escalation_count to InterruptedSession (incremented on each pause). On 3rd trigger, force rejection or escalate to human support.
- Q: On-timeout escalation (admin forgets to review) → A: After 30 min without review: send Telegram to customer "Review in progress, will respond shortly." After 60 min: escalate to human support queue (handoff to human agent, not auto-reject).
- Q: Dry-run validation (admin deletes required fields) → A: Full Pydantic schema validation at /review endpoint before accepting edit. Validate against downstream node's TypedDict (required field presence + type). Reject if missing required fields.

### Session 2026-03-06 (Round 3 — Implementation Complexity)

- Q: QueuedMessage processing architecture → A: On graph resume, `queue_consumer_node` runs first (before order logic). It reads all unprocessed QueuedMessages in timestamp order, classifies intent, and either merges follow-ups into conversation_history or routes to a cancellation/modification path.
- Q: QueuedMessage cleanup strategy → A: Soft delete with 90-day archive. Mark processed messages (processed=true) in DB. Auto-archive old entries after 90 days. Retain for audit/support investigation.
- Q: Human support queue definition → A: Postgres SupportQueue table (session_id, reason, created_at, assigned_to, status). Source of truth for escalations. Week 6 builds UI/Telegram integration. Week 4 focuses on populating this table (not the UI).
- Q: Rejection flow & customer communication → A: Route to customer_support_node, NOT immediate __end__. Node composes empathetic response explaining reason and offering support contact. Then __end__. Customer feels acknowledged, not abandoned.
- Q: Synthetic message placement (strength/positioning) → A: Inject after customer's last message (before any previous AI response), not at tail. Positions correction in narrative flow. LLM sees it as present-day context, not tail annotation.

### Session 2026-03-06 (Round 4 — Disruptive Messages & Gateway Design)

- Q: What prevents the graph from being invoked on a paused session when a new message arrives? → A: The Paused Session Gateway in the FastAPI webhook handler checks HITLMetadata.status **before** invoking LangGraph. If status = "paused", the handler writes to QueuedMessage and returns auto-reply immediately. The graph is never touched.
- Q: What does GET /state return — should admin see queued messages? → A: Yes. `GET /graph/{session_id}/state` returns graph state (from PostgresSaver) + HITLMetadata + all unprocessed QueuedMessages. Admin must see what the customer said during the pause before making a decision.
- Q: How does the graph handle queued messages that signal cancellation or order change? → A: `queue_consumer_node` runs first on resume. It classifies queued message intent using a cheap model. CANCEL/ABORT → route to `cancellation_node` (bypasses order execution). MODIFY_ORDER → update state + re-pause at order_node (new HITL cycle). CONFIRM/FOLLOW_UP → merge as "Customer followed up: [text]" and continue to order_node.
- Q: What happens to queued messages when a session is escalated to SupportQueue at 60 min? → A: All unprocessed QueuedMessages for the session are included in context_snapshot in the SupportQueue record. New messages after escalation still queue (gateway routes to QueuedMessage regardless of escalated status). Support agent has full message history.
- Q: Does the Webhook Gateway change behavior after escalation (status="escalated")? → A: No special casing needed — gateway queues all messages whenever status is not "active". Both "paused" and "escalated" sessions receive the same auto-reply and message queuing treatment.

### Session 2026-03-07 (Round 5 — Hidden Corners & Production Hardening)

- Q: Double Correction — Admin manually applies MODIFY_ORDER change via state_edits, then queue_consumer_node sees the same message and tries to apply it again (re-pausing or double-correcting). How to prevent? → A: Add `acknowledged_message_ids[]` to the POST /review payload. Admin explicitly marks which queued message IDs they have already addressed via state_edits. queue_consumer_node skips messages flagged as `acknowledged_by_admin=True` (marks them processed=True without re-routing). As a secondary safety: queue_consumer_node checks whether a MODIFY_ORDER change from a queued message is already reflected in the current state before routing — if state already matches, treat as CONFIRM/FOLLOW_UP.
- Q: Dangling Tool Call — If queue_consumer_node routes to cancellation_node mid-order, any pending tool call in conversation history (AIMessage.tool_calls with no matching ToolMessage) will cause LLM API errors. How to handle? → A: queue_consumer_node MUST perform an orphaned tool call scan as its **first step**, before any intent classification or routing. For each orphaned tool call (tool_call_id with no corresponding ToolMessage), inject a synthetic ToolMessage: `{tool_call_id: X, content: "Operation cancelled: customer request changed during admin review."}`. This closes the tool chain regardless of routing outcome.
- Q: Stale Data — Admin approves after 45 min, but inventory has changed (out of stock) or price has been updated. Approved state reflects reality at pause time, not now. → A: Add `state_freshness_validator_node` between queue_consumer_node and order_node. On resume, re-query inventory counts and current prices for all order items. Out-of-stock → auto-reject (reason="inventory_changed"), route to customer_support_node. Price delta < 5% → auto-correct + append synthetic "SYSTEM [Stale Data]" message, continue. Price delta ≥ 5% → re-pause (new HITL cycle, increments escalation_count).
- Q: Double Human Interference — Admin comes back after session was escalated to SupportQueue and tries to approve via /review. Both admin and support agent are now acting. → A: /review endpoint MUST return HTTP 409 Conflict if HITLMetadata.status = "escalated". Error: "Session escalated to support queue. Admin action locked." Once escalated, only the /support endpoint (Week 6) can act on the session.
- Q: Multiple request_edits to the same field produce contradicting synthetic messages. → A: When request_edit targets a field that already has a pending synthetic system_override message, REPLACE the existing synthetic message for that field (match by field name + pause_id) rather than appending a new one. The conversation history will always contain exactly one synthetic message per field per pause cycle.
- Q: Admin approves despite seeing a CANCEL in GET /state queued messages. Which wins? → A: Customer's explicit cancellation always takes precedence. queue_consumer_node's CANCEL routing is unconditional — it fires regardless of what action the admin chose. Admin's "approve" is an instruction to resume the graph; it does NOT override the customer's intent discovered in queue_consumer_node. This is documented as an explicit design decision.
- Q: Mixed-intent batch — Customer sends 3 conflicting messages: "change to L", "never mind M is fine", "cancel everything". How to classify? → A: queue_consumer_node sends ALL queued messages as a single batch to the intent classifier (not one-by-one). The classifier evaluates the net intent of the full batch; messages are weighted by recency (the final message in temporal order carries the most weight). This prevents early messages from overriding the customer's final intent.
- Q: queue_consumer_node crashes after marking some messages processed=True but before completing routing — partial consumption creates orphaned "done" messages. → A: QueuedMessage processing MUST be wrapped in a database transaction. Marking processed=True, setting acknowledged_by_admin, and recording the routing decision are committed atomically. On crash/retry, messages revert to unprocessed state and the entire batch is retried.
- Q: Ghost "approved" state — HITLMetadata is set to "approved" but graph.invoke(Command(resume)) subsequently fails. Admin cannot re-approve (status="approved") but the graph never ran. → A: Add "resuming" as a transient HITLMetadata status. Set status="resuming" immediately before calling graph.invoke(). If invoke succeeds, the graph itself transitions status on completion. If invoke fails, the /review handler catches the exception and reverts status to "paused" with an error logged. This prevents a dead "approved" state.
- Q: Does escalation_count reset after each successful admin approval? → A: No. escalation_count is cumulative for the lifetime of the order — it counts every HITL pause the order has triggered, regardless of approval/rejection outcomes. It never decrements. This is the mechanism that prevents the infinite loop: pause → approve → pause → approve → ... escalation_count will reach the cap (2) after 2 cycles regardless of outcomes.

---

## Overview

The HITL (Human-in-the-Loop) system is a **business-critical risk control mechanism** that pauses the AI agent's execution before performing sensitive, revenue-affecting, or legally-significant actions. An admin reviews the AI's **intended next action**, can modify the proposed state, and explicitly approve/reject before execution resumes. This ensures the AI cannot autonomously cause financial harm or compliance violations.

**Core Principles**:
1. **Gateway-first**: The FastAPI webhook handler checks session pause status *before* invoking LangGraph. A paused session never receives a new graph invocation from Telegram input — this is the single point of control for all disruptive message scenarios.
2. **Inside the graph**: HITL is placed inside the LangGraph state machine (not patched into FastAPI controllers), and activates *before* side-effects occur — not after damage is done.
3. **Queue consumer as first node**: On every graph resume after admin approval, `queue_consumer_node` executes first. It closes orphaned tool calls, classifies queued messages as a batch, and resolves intent before any business logic runs.
4. **Customer intent overrides admin approval**: A customer's explicit cancellation or modification detected in `queue_consumer_node` takes precedence over the admin's approval action. Admin approval means "resume the graph" — it does not dictate business outcome.
5. **Freshness before execution**: `state_freshness_validator_node` runs after queue_consumer_node to re-validate inventory and pricing before any side-effects. Stale state from a long review window is caught and handled before orders are placed.
6. **Resuming as a transient safety status**: HITLMetadata uses a "resuming" transient state between admin approval and successful graph.invoke(). This prevents ghost-approved sessions that can never be re-reviewed.

---

## Architecture

### System Flow (5 Layers)

```
[Telegram Message]
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 1 — Paused Session Gateway (FastAPI Webhook Handler) │
│                                                             │
│  Lookup HITLMetadata.status for session_id                  │
│                                                             │
│  status ≠ "active" ──► Queue message (QueuedMessage table)  │
│                         Send auto-reply (< 2s)              │
│                         STOP — do NOT invoke graph          │
│                                                             │
│  status = "active"  ──► continue to Layer 2                 │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 2 — LangGraph Execution (HITL Guard at Router)       │
│                                                             │
│  Graph runs; when ORDER_PLACEMENT intent detected:          │
│  hitl_guard_node calls interrupt() if confidence < 0.7      │
│  OR cost > 8000 tokens → execution frozen in checkpointer   │
│                                                             │
│  On pause:                                                  │
│  ├── Increment InterruptedSession.escalation_count          │
│  ├── escalation_count ≥ 3 → force reject → support_node    │
│  ├── Write HITLMetadata(status="paused", paused_at=now)     │
│  └── Notify admin                                           │
└─────────────────────────────────────────────────────────────┘
       │ (graph frozen; webhook returns)
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 3 — Admin Review (/review API)                       │
│                                                             │
│  GET /graph/{session_id}/state                              │
│    Returns: graph_state + hitl_metadata + queued_messages[] │
│                                                             │
│  POST /review { action, state_edits, acknowledged_message_ids[], reason, version }
│    ├── status = "escalated" ──► HTTP 409 Conflict           │
│    │     "Session escalated to support queue. Locked."      │
│    │                                                        │
│    ├── "approve":                                           │
│    │     1. Validate version (optimistic lock)              │
│    │     2. Pydantic validate + apply state_edits           │
│    │     3. Append/replace synthetic messages per edit      │
│    │     4. Mark acknowledged_message_ids as                │
│    │        acknowledged_by_admin=True (atomic DB write)    │
│    │     5. Set HITLMetadata(status="resuming")             │
│    │     6. graph.invoke(Command(resume=None))              │
│    │        → on success: continue to Layer 4               │
│    │        → on failure: revert status to "paused"         │
│    │                                                        │
│    ├── "reject":                                            │
│    │     1. Set HITLMetadata(status="rejected")             │
│    │     2. graph.update_state(rejection_reason)            │
│    │     3. graph.invoke() → customer_support_node → end    │
│    │                                                        │
│    └── "request_edit":                                      │
│          1. Pydantic validate edits                         │
│          2. graph.update_state() with edits                 │
│          3. Append OR REPLACE synthetic message per field   │
│             (replace if field already has pending override) │
│          4. Remain paused (explicit approve required next)  │
└─────────────────────────────────────────────────────────────┘
       │ (approve path only, status="resuming")
       ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 4 — queue_consumer_node (First Node on Resume)       │
│                                                             │
│  Step 1: Orphaned Tool Call Scan (ALWAYS, before routing)  │
│  ├── Scan conversation history for AIMessage.tool_calls     │
│  │   with no matching ToolMessage in history                │
│  └── For each orphan: inject synthetic ToolMessage          │
│      {tool_call_id: X, content: "Operation status reset:   │
│       customer request changed during review pause."}       │
│                                                             │
│  Step 2: Load & Classify Queued Messages (atomic tx)        │
│  ├── Load QueuedMessages(processed=False,                   │
│  │   acknowledged_by_admin=False, order by received_at ASC) │
│  ├── Send ALL messages as one batch to intent classifier    │
│  │   (final temporal message weighted highest)              │
│  └── Classify net intent of batch                           │
│                                                             │
│  No messages       ──► continue to Layer 4.5               │
│                                                             │
│  CANCEL/ABORT      ──► close orphans (already done)         │
│                         mark all messages processed=True    │
│                         route to cancellation_node          │
│                         (overrides admin's approve intent)  │
│                                                             │
│  MODIFY_ORDER      ──► check: does current state already    │
│                         reflect the requested change?       │
│                         YES → treat as CONFIRM/FOLLOW_UP    │
│                         NO  → update state + re-pause       │
│                               (increment escalation_count)  │
│                                                             │
│  CONFIRM/FOLLOW_UP ──► Merge as "Customer followed up: [x]" │
│                         Mark processed=True (all, in tx)    │
│                         continue to Layer 4.5               │
│                                                             │
│  (all DB writes in single transaction; rollback on failure) │
└─────────────────────────────────────────────────────────────┘
       │ (non-cancel path only)
       ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 4.5 — state_freshness_validator_node                 │
│                                                             │
│  Re-query current inventory and pricing for all order items │
│                                                             │
│  Item out of stock:                                         │
│  └── Auto-reject (reason="inventory_changed_during_review") │
│      → customer_support_node → end                          │
│                                                             │
│  Price delta < 5% of paused-state price:                    │
│  └── Auto-correct state + append synthetic                  │
│      "SYSTEM [Stale Data]: price updated X→Y" message       │
│      → continue to order_node                               │
│                                                             │
│  Price delta ≥ 5%:                                          │
│  └── Re-pause at order_node (new HITL cycle)                │
│      increment escalation_count                             │
│      Notify admin of price change reason                    │
└─────────────────────────────────────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────────────────────────────┐
│  Layer 5 — Timeout & Escalation (Background Scheduler)      │
│                                                             │
│  paused_at + 30 min:                                        │
│    → Send Telegram to customer (idempotent per session)     │
│    → Set HITLMetadata.timeout_notified_at                   │
│                                                             │
│  paused_at + 60 min:                                        │
│    → Insert SupportQueue record with context_snapshot       │
│      (includes all unprocessed QueuedMessages)              │
│    → Set HITLMetadata(status="escalated")                   │
│    → Gateway continues queuing new messages (no change)     │
│    → /review now returns 409 for this session               │
└─────────────────────────────────────────────────────────────┘
```

---

## User Scenarios & Testing

### User Story 1 - Admin Reviews & Approves Order (Priority: P1)

A customer requests to place an order for 10 units of Product A. The AI agent collects the order details (product, quantity, customer info) and reaches the `order_node`. The graph pauses and flags the order for human review. The admin opens the `/review` endpoint, sees the full order state (items, total cost, customer confirmation status), verifies correctness, and approves. The graph resumes via `queue_consumer_node` (no queued messages) then executes the order.

**Why this priority**: Ordering is the primary revenue-generating action; approving orders is the #1 HITL use case for SMEs. Implementing this unlocks the entire HITL system.

**Independent Test**: Construct a sample order, halt at `order_node`, fetch state, approve via `/review`, confirm `queue_consumer_node` runs (no messages), confirm order executes.

**Acceptance Scenarios**:

1. **Given** the AI has collected valid order details, **When** the graph reaches `order_node`, **Then** execution pauses, HITLMetadata status is set to "paused", and an interruption flag is set in the state
2. **Given** an interruption is active, **When** admin calls `GET /graph/{session_id}/state`, **Then** they receive the graph state, next node name, HITLMetadata, and queued_messages array (empty if none)
3. **Given** the current state is displayed, **When** admin approves via `POST /review` with action "approve", **Then** `queue_consumer_node` runs first, finds no queued messages, and graph continues to `order_node` where order is persisted
4. **Given** a paused order, **When** admin modifies the proposed order total and approves, **Then** the modified state is persisted and a synthetic system message is appended to conversation history before resumption
5. **Given** the graph is paused awaiting admin review, **When** the customer sends additional messages via Telegram, **Then** the Paused Session Gateway intercepts the webhook, writes to QueuedMessage table, sends auto-reply "Your order is being reviewed (est. 2 min). We'll respond shortly." within 2 seconds, and does NOT invoke the graph

---

### User Story 2 - Admin Rejects Order & Provides Reason (Priority: P1)

The AI has prepared an order, but the admin notices the customer's credit limit has been exceeded. The admin rejects the order via the `/review` endpoint with reason "Customer credit limit exceeded". The graph updates state with the rejection, resumes, and routes to `customer_support_node` which composes an empathetic response before ending.

**Why this priority**: Rejection is as critical as approval — the system must handle denials gracefully without customer confusion.

**Independent Test**: Construct an order, reject it with a reason, verify routing to `customer_support_node`, verify customer-facing response contains the rejection reason.

**Acceptance Scenarios**:

1. **Given** an order is paused at `order_node`, **When** admin calls `POST /review` with action "reject" and a reason, **Then** HITLMetadata status is set to "rejected" and rejection reason is persisted in state
2. **Given** the order is rejected, **When** the graph resumes, **Then** execution routes to `customer_support_node` (not `__end__` directly), which composes an empathetic message including the rejection reason, then ends
3. **Given** rejection is logged, **When** admin or AI queries the session history, **Then** rejection reason, admin_user_id, and timestamp are retrievable from the audit log

---

### User Story 3 - Admin Requests Edit & Re-Review (Priority: P1)

The AI proposes a final price of 1,200,000 VND, but the admin notices a calculation error. The admin uses `request_edit` to correct the price to 1,100,000 VND. The state updates, a synthetic system message is appended, and the session remains paused. The admin then explicitly approves, the graph resumes, `queue_consumer_node` finds no queued messages, and the corrected order is placed.

**Why this priority**: Edit capability prevents admins from rejecting and restarting orders; it streamlines the approval workflow.

**Independent Test**: Pause an order, `request_edit` with price change, verify synthetic message appended, then approve, verify order uses corrected price.

**Acceptance Scenarios**:

1. **Given** a paused order state, **When** admin calls `POST /review` with action "request_edit" and edits (e.g., price field), **Then** state is updated with new values and HITLMetadata status remains "paused"
2. **Given** state is edited via `request_edit`, **When** admin provides explicit approval (action "approve") as a separate call, **Then** graph resumes with the modified state from the same interrupted node
3. **Given** edits are applied, **When** order is persisted, **Then** the final order reflects admin-corrected values, not the original AI proposal
4. **Given** admin edits state with invalid field type (e.g., price as string), **When** `/review` validates the request, **Then** endpoint returns Pydantic validation error immediately; state is unchanged; admin must fix and resubmit

---

### User Story 4 - Customer Sends "Cancel" During HITL Pause (Priority: P1)

The AI has paused at `order_node` awaiting admin review. While waiting, the customer sends "actually, cancel the order." The Paused Session Gateway queues this message. The admin views `GET /state` and sees the queued cancellation message alongside the order state. Admin approves (trusting `queue_consumer_node` to handle the intent). On resume, `queue_consumer_node` classifies the message as CANCEL and routes to `cancellation_node` instead of executing the order.

**Why this priority**: This is the primary "disruptive message" scenario. Without the gateway + queue_consumer_node, the cancellation would be lost or cause a race condition.

**Independent Test**: Pause an order, simulate a "cancel" message via Telegram webhook, verify it is queued (graph not invoked), admin approves, verify `queue_consumer_node` routes to `cancellation_node` and order is NOT placed.

**Acceptance Scenarios**:

1. **Given** a session is paused, **When** a Telegram webhook arrives with "cancel order", **Then** the Paused Session Gateway writes the message to QueuedMessage and returns auto-reply within 2 seconds; the graph is not invoked
2. **Given** a queued cancellation message, **When** admin calls `GET /graph/{session_id}/state`, **Then** the response includes the queued message text so admin can see the customer's intent before deciding
3. **Given** admin approves and graph resumes, **When** `queue_consumer_node` classifies the queued message as CANCEL, **Then** execution routes to `cancellation_node`; the order is NOT placed; a cancellation confirmation is sent to the customer
4. **Given** the cancellation is processed, **When** all QueuedMessages for this cycle are consumed, **Then** each message is marked processed=True

---

### User Story 5 - Confidence Guard Triggers HITL Automatically (Priority: P2)

The AI searches for product information but retrieves results with low confidence (similarity score: 0.65, below the 0.7 threshold). Instead of answering with low-confidence data, the system automatically escalates to human review. An admin reviews the query, retrieves the correct information manually, and provides it to the customer.

**Why this priority**: Prevents hallucinations; protects customer trust. Confidence guards are a safety mechanism, not optional.

**Independent Test**: Submit a query that yields low-confidence results, verify automatic HITL escalation, confirm no low-confidence answer reaches the customer without human verification.

**Acceptance Scenarios**:

1. **Given** the AI retrieves a response with confidence < 0.7, **When** the response is prepared, **Then** execution is automatically interrupted and HITLMetadata is written with pause_reason="low_confidence"
2. **Given** a low-confidence escalation, **When** admin accesses `GET /state`, **Then** they see the query, retrieved data, confidence score, and queued_messages array
3. **Given** admin provides a manual answer via `POST /review` with action "approve" and state_edits containing the verified answer, **Then** the customer receives the admin-provided answer marked "Verified by support team"

---

### User Story 6 - Cost Guard Triggers HITL for Expensive Operations (Priority: P2)

The AI is about to call an expensive LLM model estimated at 10,000 tokens, exceeding the SME's per-operation budget threshold (8,000 tokens). The system escalates for human approval before incurring the cost. The admin reviews and decides whether the operation is worth the cost.

**Why this priority**: SMEs are extremely cost-sensitive. Cost guards prevent runaway expenses and show engineering maturity (cost-aware AI).

**Independent Test**: Simulate an operation exceeding the cost threshold, verify escalation, confirm the operation is blocked until approved.

**Acceptance Scenarios**:

1. **Given** an operation is estimated to cost > threshold tokens, **When** the graph reaches the decision node, **Then** execution is interrupted and HITLMetadata is written with pause_reason="cost_limit"
2. **Given** a cost escalation, **When** admin reviews via `GET /state`, **Then** they see the operation description and estimated cost (calculated from compressed per-operation context, not full conversation history)
3. **Given** admin rejects due to cost, **When** the graph resumes, **Then** an alternative (cheaper) operation path is attempted or the user is offered a simpler solution

---

### User Story 7 - Failure Recovery from "Resuming" State (Priority: P1)

The admin approves an order at the `/review` endpoint. The handler sets HITLMetadata.status="resuming" and calls `graph.invoke()` to run the paused graph. Due to a temporary database connection failure, the graph.invoke() raises an exception (PostgresConnectionError). The `/review` handler catches this exception, reverts HITLMetadata.status back to "paused", logs the failure, and returns HTTP 500 to the admin. The admin sees the error message "Graph execution failed — please retry" and can safely click Retry. The session is never left in a dead "approved but not running" state (ghost-approved state is prevented).

**Why this priority**: Failure recovery is critical for production reliability. Without the "resuming" transient state and revert logic, a network hiccup could permanently break the workflow (admin approval stuck, session unusable, requires manual database intervention).

**Independent Test**: Mock graph.invoke() to raise an exception, call POST /review with action="approve", verify HITLMetadata.status reverts to "paused" and HTTP 500 is returned to admin.

**Acceptance Scenarios**:

1. **Given** admin calls POST /review with action="approve", **When** the /review handler sets HITLMetadata.status="resuming" before calling graph.invoke(), **Then** this state change is persisted immediately (no batching)
2. **Given** graph.invoke() raises an exception (e.g., database connection lost), **When** the /review handler catches the exception, **Then** it immediately reverts HITLMetadata.status back to "paused" and returns HTTP 500 with error detail to the admin
3. **Given** status is reverted to "paused" after failed invoke, **When** the admin retries by clicking Retry or calling POST /review again with the same payload, **Then** no conflict occurs (idempotency applies; second approval with same reasoning is safe)
4. **Given** a failed graph invocation is logged, **When** support team reviews logs, **Then** they can see the exact exception, timestamp, session_id, admin_user_id, and the number of retry attempts

---

### Edge Cases

- **Concurrent approval attempts (double-click due to lag)**: The InterruptedSession `version` field enforces optimistic locking. The first approval increments version and succeeds; the second with a stale version is rejected: "This order was already approved by [admin_user_id]." The error message includes the approver's identity.
- **Admin approval times out (30 min)**: Customer receives Telegram notification "Your order is being reviewed. We'll respond shortly." Session remains paused; resumption still requires explicit admin decision.
- **Admin approval times out (60 min)**: Session escalated to SupportQueue. HITLMetadata status set to "escalated". All unprocessed QueuedMessages included in context_snapshot. Gateway continues queuing new messages unchanged. /review now returns HTTP 409.
- **Admin edits state with invalid data**: Full Pydantic validation at `/review` endpoint (type AND required field presence). Error returned immediately; state unchanged. Semantic validation (price > 0) deferred to graph resumption — if invalid, graph pauses again with the semantic error for admin to fix.
- **Zombie sessions (abandoned without review)**: After 24 hours with no admin action post-escalation, HITLMetadata status set to "abandoned". SupportQueue record remains for manual resolution. No auto-approval ever occurs.
- **Customer sends messages after escalation (status="escalated")**: Gateway treats "escalated" same as "paused" — messages are queued with auto-reply. No special casing needed.
- **Cascading HITL (multiple sensitive nodes in sequence)**: escalation_count increments on each pause and is **never reset**, tracking the order's lifetime pause count. On the 3rd trigger, the system checks escalation_count ≥ 3 before calling interrupt() in `hitl_guard_node`; if true, it bypasses the interrupt and forces the rejection path directly to `customer_support_node`
- **Missing confidence score**: Treated as 0.0; system escalates conservatively to HITL with pause_reason="low_confidence".
- **Double Correction — Admin applies queued change manually, queue_consumer_node re-applies it**: Admin sees a queued "change to size L" message in GET /state and manually applies it via state_edits before approving. On resume, queue_consumer_node loads the queued message. It first checks: is this message in `acknowledged_message_ids[]`? If yes, skip re-routing (mark processed, treat as CONFIRM). Separately: does the current state already reflect the requested change? If yes, treat as CONFIRM. Both checks prevent double correction.
- **Dangling Tool Call — CANCEL routing leaves orphaned tool call in history**: queue_consumer_node always runs the orphaned tool call scan (Step 1) before any routing decision. This is unconditional — it runs even if there are no queued messages to process. LLM API contracts are never violated.
- **Stale Data — Inventory or price changed during review window**: `state_freshness_validator_node` catches this on every resume. Inventory-based rejection is clean (customer is informed). Minor price corrections auto-apply. Significant price changes re-pause the session (this consumes an escalation_count slot).
- **Double Human Interference — Admin acts after session escalated**: /review returns HTTP 409 the moment HITLMetadata.status = "escalated". This is a hard state machine enforcement. Admin must contact the support agent, not act directly.
- **Multiple request_edits to same field**: Admin calls request_edit for `price` field at 1.1M, then again at 1.2M (correction of the correction). The second request_edit matches by `(field_name, pause_id)` and replaces the existing synthetic message for `price`. Conversation history always has exactly one synthetic message per (field, pause cycle).
- **Customer sends CANCEL but admin has already approved**: queue_consumer_node's CANCEL routing is unconditional and fires regardless of what action the admin chose. The admin's "approve" action means "resume the graph" — it does not dictate that the order must be placed. Customer intent discovered in queue_consumer_node takes precedence.
- **Mixed-intent queued messages**: Customer sends 3 messages: "change to L" → "never mind, keep M" → "actually cancel." queue_consumer_node sends all 3 as a single batch to the intent classifier with recency weighting. The net intent is CANCEL (final message). No routing confusion from the earlier contradictory messages.
- **queue_consumer_node crashes mid-batch**: All QueuedMessage state changes (processed=True, acknowledged_by_admin) and routing decisions are committed in a single database transaction. On crash, the transaction rolls back; all messages revert to unprocessed; the node retries the entire batch on the next invocation.
- **graph.invoke() fails after admin approves (ghost approved state)**: The /review approve handler sets status="resuming" before calling graph.invoke(). If invoke raises an exception, the handler catches it and reverts HITLMetadata status to "paused". Admin receives a 500 error and can safely retry. No session is left in a permanent "approved but not running" state.
- **state_freshness_validator_node triggers escalation_count on price delta ≥ 5%**: This is a legitimate re-pause, so escalation_count increments. If the order was already at escalation_count=2, this re-pause would push it to 3, triggering force rejection. To prevent inventory fluctuations from force-rejecting valid orders, state_freshness_validator_node writes the re-pause reason as "stale_data" — the max HITL logic can treat "stale_data" pauses as non-escalating (does not increment counter). This is a variant: counter only increments for human-triggered or intent-change pauses, not data freshness pauses.
- **Admin acting on an already-completed session**: If admin calls /review on a session where HITLMetadata.status = "approved" or "rejected", the endpoint returns HTTP 409 "Session already resolved." Idempotency applies only to exact-same-payload repeats, not to acting on a terminal status.

---

## Requirements

### Functional Requirements

- **FR-001**: LangGraph MUST use dynamic `interrupt()` mechanism inside `hitl_guard_node` (NOT static `interrupt_before`) to pause execution at a single dynamic checkpoint. Reason: `interrupt_before` is static and prevents routing to `queue_consumer_node` on resume (graph would skip the queued message processing logic). `interrupt()` allows `Command(goto="queue_consumer_node")` routing, enabling proper message consumption and intent classification before downstream nodes execute. `hitl_guard_node` is triggered when confidence < 0.7, cost > 8000 tokens, or order requires approval
- **FR-002**: `GET /graph/{session_id}/state` MUST return the current graph state (from PostgresSaver), the next node name, HITLMetadata, and all unprocessed QueuedMessages for the session. Admin must see queued messages before making a review decision.
- **FR-003**: `POST /review` MUST support three actions: "approve", "reject", "request_edit". All actions MUST be idempotent (safe to call multiple times with same payload).
- **FR-004**: Admin MUST be able to edit specific fields in the paused state via `request_edit`; changes MUST pass full Pydantic schema validation (required field presence + type checking); changes MUST be persisted; session MUST remain paused after edit until explicit "approve" action.
- **FR-005**: Confidence scores (similarity, rerank, or model-provided) MUST be calculated explicitly and stored in state; if overall_confidence < 0.7, the system MUST automatically trigger HITL with pause_reason="low_confidence"
- **FR-006**: Cost estimates MUST be calculated before executing expensive operations using per-operation compressed context; if estimated cost > threshold, execution MUST pause with pause_reason="cost_limit"
- **FR-007**: All state edits, approvals, and rejections MUST be logged to ReviewAction table with timestamp, admin_user_id, action type, state_edits diff, and reason
- **FR-008**: A resumed graph MUST use the updated/approved state from PostgresSaver, not recompute from original input; `queue_consumer_node` runs as the first node on every resume after approval
- **FR-009**: Rejection MUST include a reason field stored in state; `customer_support_node` MUST access this reason when composing the customer-facing response
- **FR-010**: HITLMetadata.status field MUST distinguish "paused" | "approved" | "rejected" | "escalated" | "abandoned" to prevent re-execution of rejected orders and enable the Gateway to correctly classify sessions
- **FR-011**: InterruptedSession MUST include a `version` field for optimistic locking; concurrent approvals with stale version MUST be rejected; only the first approval succeeds
- **FR-012**: The Paused Session Gateway (FastAPI webhook handler) MUST check HITLMetadata.status **before** invoking LangGraph. If status ≠ "active", the handler MUST write to QueuedMessage, send auto-reply to customer within 2 seconds, and return without invoking the graph.
- **FR-013**: Full graph state MUST be retrieved exclusively from LangGraph PostgresSaver (checkpointer). HITLMetadata stores pause metadata only (reason, admin_id, timestamps, escalation_count, status). No state duplication.
- **FR-014**: When admin edits state, a synthetic system message MUST be appended to conversation_history: `"SYSTEM [Admin override]: [field] updated from [old_value] to [new_value]."` Synthetic messages MUST be positioned immediately after the customer's last message (not at history tail).
- **FR-015**: HITL escalation_count MUST be tracked per session; maximum 2 HITL pauses allowed per order; on 3rd escalation trigger the graph MUST skip the pause and route directly to rejection/support escalation
- **FR-016**: If order is not reviewed within 30 minutes of pause, customer MUST receive exactly one Telegram notification (idempotent). If not reviewed within 60 minutes, one SupportQueue record MUST be inserted (idempotent on session_id) and HITLMetadata status set to "escalated"
- **FR-017**: `queue_consumer_node` MUST run as the first node on every graph resume after admin approval. It MUST load all QueuedMessages(processed=False) ordered by received_at ASC and classify intent using a cheap model: CANCEL/ABORT → cancellation_node; MODIFY_ORDER → update state + re-pause; CONFIRM/FOLLOW_UP → merge into history + continue to order_node. All consumed messages MUST be marked processed=True.
- **FR-018**: Rejection flow MUST route to `customer_support_node`, NOT immediately to `__end__`. The node MUST compose an empathetic message: "We're unable to process your order because [reason]. Our support team will help. Contact: [support link]." Then transition to `__end__`.
- **FR-019**: SupportQueue table MUST be populated on every escalation event (timeout_60min, max_hitl_exceeded, rejected order with SupportQueue flag, low-confidence refund). context_snapshot MUST include order details, customer info, and all unprocessed QueuedMessages at time of escalation.
- **FR-020**: Synthetic messages MUST be inserted immediately after the customer's last message in conversation_history, not appended at tail. Structure: `{type: "system_override", timestamp, field, old_value, new_value}`.
- **FR-021**: QueuedMessage cleanup: Mark processed=True immediately after consumption by `queue_consumer_node`. Auto-archive (archived=True) after 90-day retention window. Archived messages are read-only. No hard delete. Archive job runs nightly; archived messages remain queryable for 30 days post-archive.
- **FR-022**: When HITLMetadata status transitions to "escalated", all unprocessed QueuedMessages for the session MUST be included in the SupportQueue.context_snapshot. New messages received after escalation MUST continue to be queued (Gateway behavior unchanged). Support agents MUST be able to query the live QueuedMessage table to see messages received after the initial escalation snapshot — the context_snapshot is a historical record, but live messages are fetched from the database on-demand (not polled into the session record after escalation).
- **FR-023**: `queue_consumer_node` MUST classify ALL queued messages as a single batch using the cheap/economy model (not premium). The batch classifier receives entire message list in one call and weights messages by recency — the final message carries most weight for net intent determination. If classifier confidence < 0.6 on the net intent, the system MUST default conservatively to CONFIRM/FOLLOW_UP. (Replaces separate FR-029; consolidated for clarity.)
- **FR-024**: `POST /review` payload for "approve" action MUST accept an optional `acknowledged_message_ids[]` field (list of QueuedMessage UUIDs). Before graph.invoke(), the handler MUST atomically mark those QueuedMessages as `acknowledged_by_admin=True`. `queue_consumer_node` MUST skip messages where `acknowledged_by_admin=True` (mark processed=True, no routing). As a secondary check, queue_consumer_node MUST also compare MODIFY_ORDER intent against current state — if the change is already reflected, treat as CONFIRM/FOLLOW_UP regardless of acknowledgment flag.
- **FR-025**: `queue_consumer_node` MUST perform an **orphaned tool call scan** as its absolute first step, before any intent classification or routing. Scan MUST be limited to the most recent 10–20 AIMessages for performance (do not scan entire conversation history). For each AIMessage in the limited window with tool_calls that have no corresponding ToolMessage, inject a synthetic ToolMessage: `{tool_call_id: X, content: "Operation status reset: customer request changed during admin review pause."}`. This MUST run even if there are zero queued messages to process.
- **FR-026**: A `state_freshness_validator_node` MUST execute after `queue_consumer_node` and before `order_node` on every graph resume. It MUST: (a) re-query current inventory counts for all items in the order; (b) re-query current prices. Inventory insufficient → auto-reject with reason="inventory_changed_during_review", route to customer_support_node. Price delta < 5% → auto-correct state + append synthetic "SYSTEM [Stale Data]: price updated from [old] to [new]" message, continue. Price delta ≥ 5% → re-pause (write pause_reason="stale_data_price_change") without incrementing escalation_count (stale data pauses are excluded from the escalation cap).
- **FR-027**: `POST /review` MUST return HTTP 409 Conflict with body `{"error": "Session escalated to support queue. Admin action locked.", "assigned_to": [support_agent_id_or_null]}` if HITLMetadata.status = "escalated". No admin action (approve/reject/edit) is permitted on an escalated session.
- **FR-028**: When `request_edit` targets a field that already has a pending synthetic system_override message in conversation_history for the same `(field_name, pause_id)`, the handler MUST **replace** the existing synthetic message rather than appending a new one. Conversation history MUST contain exactly one synthetic message per (field, pause cycle) at all times.
- **FR-029**: All QueuedMessage state mutations within `queue_consumer_node` (marking processed=True, acknowledged_by_admin consumption, routing decision record) MUST be committed in a single atomic database transaction. If the transaction fails or the node crashes, all messages MUST revert to their pre-processing state so the entire batch can be safely retried.
- **FR-031**: The /review "approve" handler MUST use "resuming" as a transient HITLMetadata status: (1) Set status="resuming" immediately before calling graph.invoke(). (2) If graph.invoke() raises an exception, the handler MUST catch it, revert status to "paused", log the failure, and return HTTP 500 to admin. (3) This prevents a permanently stuck "approved" session that can never be re-reviewed. The "resuming" status, if observed by the Gateway, MUST be treated as "paused" (no new graph invocations).
- **FR-032**: InterruptedSession.escalation_count MUST be treated as a cumulative lifetime counter for the order. It MUST increment on every HITL pause (natural escalations and MODIFY_ORDER re-pauses). It MUST NOT increment for "stale_data_price_change" pauses (FR-026). It MUST NEVER decrement or reset after an approval. The max HITL cap check (escalation_count ≥ 3) applies to the lifetime count.
- **FR-033**: `POST /review` MUST return HTTP 409 with body `{"error": "Session already resolved.", "status": [current_status]}` if HITLMetadata.status is "approved", "rejected", or "abandoned". These are terminal statuses. Idempotency (FR-003) applies only to repeated calls with identical payloads on a non-terminal session.

### Key Entities

- **HITLMetadata**: Lightweight table; authoritative source for session pause status. Gateway reads this on every webhook.
  - `session_id`: Which conversation
  - `pause_id`: Unique pause instance ID (UUID; multiple pauses per session allowed)
  - `pause_reason`: "order_approval" | "low_confidence" | "cost_limit" | "refund_approval" | "stale_data_price_change"
  - `paused_at`: Timestamp of pause
  - `timeout_notified_at`: Timestamp of 30-min customer notification (nullable)
  - `escalated_to_support_at`: Timestamp of 60-min escalation (nullable)
  - `status`: "active" | "paused" | "resuming" | "approved" | "rejected" | "escalated" | "abandoned"
  - Note: "resuming" is a transient status set during graph.invoke(); reverts to "paused" on failure

- **InterruptedSession**: Tracks escalation state and optimistic locking per session
  - `session_id`: Unique identifier (FK to session)
  - `next_node`: Name of the node about to execute when paused
  - `pause_reason`: Why execution was interrupted
  - `paused_at`: When interruption occurred
  - `admin_id`: ID of the reviewing admin (nullable until assigned)
  - `version`: Integer; incremented on each successful approval (optimistic lock)
  - `escalation_count`: Int (0–2); incremented each HITL pause; 3rd pause forces rejection/escalation

- **ReviewAction**: Immutable audit log of every admin decision
  - `session_id`: Which session
  - `pause_id`: Which pause instance (FK to HITLMetadata)
  - `action`: "approve" | "reject" | "request_edit"
  - `state_edits`: JSON diff of changes (nullable if no edits)
  - `acknowledged_message_ids`: JSON array of QueuedMessage UUIDs the admin explicitly handled via state_edits (nullable)
  - `reason_or_comment`: Text explanation from admin
  - `reviewed_at`: Timestamp of decision
  - `admin_user_id`: Who made the decision
  - `expected_version`: Version at time of approval; stale version → conflict error

- **QueuedMessage**: Customer messages received while a session is not "active"
  - `id`: UUID (primary key)
  - `session_id`: Which conversation
  - `message_text`: Customer's message content
  - `received_at`: Timestamp when message arrived
  - `processed`: Boolean; False until `queue_consumer_node` commits the processing transaction
  - `acknowledged_by_admin`: Boolean; True if admin listed this ID in `acknowledged_message_ids[]` in their POST /review call
  - `archived`: Boolean; True after 90-day retention window

- **ConfidenceScore**: Confidence metadata per AI turn
  - `session_id`: Which conversation
  - `turn_id`: Which AI turn
  - `similarity_score`: Vector retrieval confidence (0.0–1.0)
  - `rerank_score`: Rerank model score (optional, 0.0–1.0)
  - `model_confidence`: LLM-provided confidence (optional)
  - `overall_confidence`: Combined score (0.0–1.0)
  - `escalation_triggered`: Boolean (True if overall_confidence < 0.7)

- **SupportQueue**: Escalated sessions awaiting human support agent
  - `id`: UUID (primary key)
  - `session_id`: Which conversation (unique per active escalation)
  - `reason`: "timeout_60min" | "rejected_order" | "max_hitl_exceeded" | "low_confidence_refund"
  - `created_at`: Timestamp of escalation
  - `assigned_to`: Support agent ID (nullable until assigned)
  - `context_snapshot`: JSON — order details, customer info, rejection_reason if applicable, unprocessed QueuedMessages at escalation time
  - `status`: "pending" | "assigned" | "resolved" | "closed"

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: All order placements MUST be approved by a human before persistence; 0% of unapproved orders should reach the database
- **SC-002**: Admin MUST be able to review a paused order and make a decision within 60 seconds of retrieval; `/review` endpoint latency < 200ms
- **SC-003**: 100% of low-confidence responses (confidence < 0.7) MUST be escalated to HITL; zero low-confidence answers reach the customer without human verification
- **SC-004**: Cost guards MUST activate on 100% of operations exceeding the cost threshold; no unexpected high-cost operations execute without approval
- **SC-005**: Admin edits to paused state MUST be persisted and used for the resumed order; zero orders use pre-edit values after approval
- **SC-006**: All approvals, rejections, and edits MUST be logged in ReviewAction with full context; audit history for any order retrievable in < 1 second
- **SC-007**: Concurrent approval attempts MUST be handled via optimistic locking; exactly one approval succeeds, others fail with a conflict error
- **SC-008**: Paused sessions MUST remain paused unless explicitly resumed, rejected, or escalated; zero spontaneous resumptions
- **SC-009**: Rejection reason MUST be available to the AI for customer-facing responses; customer receives a clear, personalized explanation
- **SC-010**: HITLMetadata.status MUST always reflect the true session state ("active" | "paused" | "resuming" | "approved" | "rejected" | "escalated" | "abandoned"); the Gateway relies on this field exclusively. "resuming" is a transient status (set immediately before graph.invoke() on approve path; reverts to "paused" if invoke fails)
- **SC-011**: When graph is paused, customer MUST receive auto-reply within 2 seconds; queued messages MUST be processed completely and in order after admin decision; zero message loss
- **SC-012**: Admin state edits MUST be validated for type AND required field presence at endpoint level before acceptance; validation errors returned immediately without state modification
- **SC-013**: 100% of resumed graphs after admin edits MUST have synthetic system messages appended to conversation_history before `queue_consumer_node` runs; LLM always sees admin corrections
- **SC-014**: Maximum 2 HITL pauses per order (escalation_count ≤ 2); 3rd escalation trigger bypasses pause and forces rejection/support escalation; zero infinite HITL loops
- **SC-015**: At 30-min timeout, exactly one customer notification sent (idempotent per session). At 60-min timeout, exactly one SupportQueue record inserted (idempotent on session_id); HITLMetadata transitions to "escalated"
- **SC-016**: Full graph state stored exclusively in LangGraph PostgresSaver; HITLMetadata stores metadata only; zero state duplication between tables
- **SC-017**: On graph resume after approval, `queue_consumer_node` is the first node executed (before any business logic). All follow-up messages merged into conversation_history with "Customer followed up: [text]" prefix before proceeding to `order_node`
- **SC-018**: Rejection always routes to `customer_support_node`. Customer receives: "We're unable to process order [order_id] because [reason]. Support team will help at [link]." Then `__end__`. Zero abrupt terminations.
- **SC-019**: Every escalation event writes exactly one SupportQueue record with context_snapshot including unprocessed QueuedMessages. SupportQueue.status initially "pending"
- **SC-020**: Synthetic messages are positioned immediately after customer's last message in conversation_history (not at tail). Structure includes type, timestamp, field, old_value, new_value
- **SC-021**: QueuedMessages processed in received_at ASC order. Exactly 1 timeout notification per session (idempotent). Exactly 1 SupportQueue record per escalation (idempotent). Archived=True messages excluded from active processing. Archive job runs nightly.
- **SC-022**: When HITLMetadata transitions to "escalated", SupportQueue.context_snapshot contains all unprocessed QueuedMessages at that moment. New messages after escalation continue to queue normally via Gateway.
- **SC-023**: When `queue_consumer_node` detects a CANCEL/ABORT intent in queued messages, the order is NOT placed; execution routes to `cancellation_node`. Customer receives cancellation confirmation. Admin is not required to re-review a cancellation.
- **SC-024**: `queue_consumer_node` intent classification uses the economy/cheap model. If classifier confidence < 0.6, the node defaults to FOLLOW_UP treatment (conservative). Zero order placements blocked by ambiguous classification alone.
- **SC-025**: The Paused Session Gateway (webhook handler) is the sole control point for preventing graph invocation on paused sessions. Zero cases where the graph is invoked concurrently with a HITL pause for the same session_id.
- **SC-026**: When admin includes `acknowledged_message_ids[]` in POST /review, `queue_consumer_node` MUST skip those message IDs (mark processed=True, no routing re-evaluation). Zero double corrections caused by admin manually applying a MODIFY_ORDER change that `queue_consumer_node` then also tries to apply.
- **SC-027**: `queue_consumer_node` orphaned tool call scan runs on EVERY graph resume, unconditionally. Zero resumed graph executions have an AIMessage.tool_call without a corresponding ToolMessage. LLM API errors due to dangling tool calls: zero.
- **SC-028**: `state_freshness_validator_node` executes on every resume between queue_consumer_node and order_node. Zero orders placed against out-of-stock inventory. Price corrections of < 5% delta applied automatically before execution. Price changes ≥ 5% trigger a new HITL pause, NOT an automatic order placement with stale pricing.
- **SC-029**: `POST /review` MUST return HTTP 409 for any session with HITLMetadata.status = "escalated". Zero cases where an admin and a support agent both act on the same escalated session concurrently.
- **SC-030**: After any sequence of `request_edit` calls on the same field within a pause cycle, conversation_history contains exactly ONE synthetic system_override message for that (field, pause_id) pair. Zero duplicate or contradicting synthetic messages for the same field.
- **SC-031**: `queue_consumer_node` sends all queued messages in one batch to the intent classifier. Zero cases of the first queued message's intent overriding the customer's final stated intent. Net intent from the batch determines routing.
- **SC-032**: QueuedMessage processing is atomic. If queue_consumer_node crashes mid-batch, ALL messages revert to unprocessed (processed=False). Zero partially-consumed batches that leave some messages permanently marked processed without routing completing.
- **SC-033**: No HITL session can be permanently stuck in "approved" status without the graph having run. The "resuming" → "paused" revert on graph.invoke() failure ensures every "approved" or "rejected" status reflects an actual completed graph execution. Zero ghost-approved sessions.
- **SC-034**: InterruptedSession.escalation_count for any order is monotonically increasing throughout the order's lifetime. It never decrements, never resets between approval cycles. The value at any point reflects the total number of HITL pauses triggered for that order. Stale data pauses (pause_reason="stale_data_price_change") do NOT increment this counter.
- **SC-035**: `POST /review` returns HTTP 409 for terminal sessions (status = "approved", "rejected", "abandoned"). Admin receives clear feedback on what the terminal status is. Zero cases where an admin successfully submits a duplicate action on an already-resolved session.

---

## Assumptions

1. **LangGraph Capabilities**: LangGraph supports `interrupt_before`, `get_state()`, `update_state()`, and `Command(resume=...)` as documented
2. **Admin Authentication**: Admin users are authenticated via existing auth system before `/review` access
3. **Confidence Calculation**: Confidence scores computed in prior feature (vector similarity, rerank scores); this feature consumes those scores
4. **Cost Estimation**: Token counting and per-model pricing logic exists; this feature uses pre-computed cost estimates
5. **Async FastAPI**: All `/review` and `/state` endpoints are async and non-blocking
6. **Single Database**: All state, metadata, queued messages, and audit logs persisted in PostgreSQL only
7. **Session Management**: `session_id` uniquely identifies a conversation and is present on every Telegram webhook payload
8. **State Serialization**: Typed state (TypedDict) is fully JSON-serializable for PostgresSaver storage and retrieval
9. **HITLMetadata is pre-populated**: A "active" status record exists in HITLMetadata for all sessions at creation time, so the Gateway always finds a row to check
10. **Inventory & Pricing API**: A synchronous DB query exists to check current stock counts and prices by item ID; `state_freshness_validator_node` uses this query directly (no external HTTP calls required)
11. **Tool Call Structure**: The AI's tool calls follow a consistent structure (AIMessage.tool_calls list with id fields) that allows `queue_consumer_node` to reliably detect orphaned calls via history scan

---

## Out of Scope (Explicitly Excluded)

- **Multi-level approvals** (e.g., manager approves after admin approval) — scope is single-level approval only
- **Approval templates or pre-defined workflow rules** — the system supports flexible ad-hoc edits
- **Scheduled auto-approval** — orders will never auto-approve after a timeout; human decision is always required
- **UI dashboard** — this feature provides the `/review` REST API only; frontend/UI is Week 6+
- **Telegram-side admin notifications** — admin notification mechanism is out of scope; endpoint polling or external webhook is the admin's concern
- **Mobile app approvals** — approvals via REST API only; any mobile client is out of scope
- **queue_consumer_node ML model training** — intent classification uses an existing cheap LLM (zero-shot prompting); no fine-tuning in scope
- **/support endpoint (escalated sessions)** — the API for support agents to act on escalated SupportQueue entries is Week 6 scope; this feature only populates the SupportQueue table and enforces the 409 lock on /review for escalated sessions
- **Freshness validation for non-order nodes** — `state_freshness_validator_node` validates inventory/pricing only; it does not re-validate confidence scores, customer identity, or other non-order data at resume time
