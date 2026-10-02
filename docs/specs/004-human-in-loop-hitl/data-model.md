# Data Model: Human-in-the-Loop (HITL) Control System

**Phase 1 Output** | Branch: `004-human-in-loop-hitl` | Date: 2026-03-06  
**Schema**: `agent_v1` (existing PostgreSQL schema)

---

## 1. AgentState Extension (LangGraph TypedDict)

Extend `core/agent/state.py` — `AgentState` — with HITL fields:

```python
class HITLReasonEnum(StrEnum):
    ORDER_APPROVAL   = "order_approval"
    LOW_CONFIDENCE   = "low_confidence"
    COST_LIMIT       = "cost_limit"
    REFUND_APPROVAL  = "refund_approval"
    STALE_PRICE      = "stale_price"

class AgentState(TypedDict):
    # ... existing fields ...

    # HITL fields (Week 4)
    hitl_triggered: bool           # True when interrupt() was called
    hitl_reason: str | None        # HITLReasonEnum value
    hitl_pause_id: str | None      # UUID of HITLMetadata.pause_id
    hitl_rejection_reason: str | None   # Admin's rejection message
    hitl_escalation_count: int     # 0–1 allowed (max 2 pauses); ≥2 triggers force rejection
    hitl_approved: bool            # True after admin approval
    estimated_token_cost: int      # Tokens estimated before LLM call
```

**State Transitions**:
```
initial → hitl_escalation_count=0, hitl_triggered=False, hitl_pause_id=None
  → guard fires → hitl_triggered=True, pause_id=UUID
  → admin approves → hitl_approved=True, hitl_triggered=False
  → admin rejects → hitl_rejection_reason=str, route to customer_support_node
  → queue_consumer_node re-pause (MODIFY_ORDER) → hitl_escalation_count++ (check if ≥2 before interrupt)
  → hitl_escalation_count ≥ 2 → bypass interrupt, force rejection or SupportQueue escalation (FR-032)
```

---

## 2. HITLMetadata (New Table)

**Purpose**: Lightweight operational tracking for each HITL pause instance. NOT full state (that lives in LangGraph `checkpoints`).

**SQLAlchemy Model** (`models/schema.py`):

```python
class HITLMetadata(Base):
    __tablename__ = "hitl_metadata"
    __table_args__ = {"schema": "agent_v1"}

    pause_id: Mapped[uuid.UUID]       # Primary key (UUID v7)
    session_id: Mapped[str]           # FK → thread_id in LangGraph
    pause_reason: Mapped[str]         # HITLReasonEnum
    paused_at: Mapped[datetime]       # UTC timestamp of interrupt()
    timeout_notified_at: Mapped[datetime | None]   # 30-min notification sent
    escalated_to_support_at: Mapped[datetime | None]  # 60-min escalation
    status: Mapped[str]               # paused|approved|rejected|escalated|abandoned
    admin_id: Mapped[str | None]      # Admin who reviewed (nullable until assigned)
    escalation_count: Mapped[int]     # 0–2; enforced by FR-015
```

**Indexes**: `session_id` (query by conversation), `status + paused_at` (timeout worker queries).

**Validation Rules**:
- `escalation_count` ≤ 2 (enforced at application layer before inserting new pause)
- `status` transitions: paused → approved|rejected|escalated|abandoned ONLY (no backwards transitions)

---

## 3. ReviewAction (New Table)

**Purpose**: Immutable audit log of every admin decision. One row per approve/reject/request_edit.

```python
class ReviewAction(Base):
    __tablename__ = "review_actions"
    __table_args__ = {"schema": "agent_v1"}

    action_id: Mapped[uuid.UUID]     # Primary key (UUID v7)
    session_id: Mapped[str]
    pause_id: Mapped[uuid.UUID]      # FK → hitl_metadata.pause_id
    action: Mapped[str]              # approve|reject|request_edit
    state_edits: Mapped[dict | None] # JSONB diff: {field: {old: v, new: v}}
    reason_or_comment: Mapped[str | None]
    timestamp: Mapped[datetime]
    admin_user_id: Mapped[str]
    expected_version: Mapped[int]    # Optimistic lock version sent by client
    idempotency_key: Mapped[str]     # X-Idempotency-Key header value (unique constraint)
    acknowledged_message_ids: Mapped[list[uuid.UUID]]  # QueuedMessage IDs admin has addressed via state_edits (prevents Double Correction)
```

**Indexes**: `session_id`, `pause_id`, `idempotency_key` (UNIQUE — for idempotent replay).

**Validation Rules**:
- `action` must be one of: `approve`, `reject`, `request_edit`
- `state_edits` required if `action == "request_edit"`
- `expected_version` must match current `InterruptedSession.version` (conflict check before insert)
- `acknowledged_message_ids` is empty list `[]` if no messages acknowledged; populated if admin used state_edits to address queued messages

---

## 4. QueuedMessage (New Table)

**Purpose**: Customer messages received while graph is paused. Consumed by `queue_consumer_node` on resume.

```python
class QueuedMessage(Base):
    __tablename__ = "queued_messages"
    __table_args__ = {"schema": "agent_v1"}

    message_id: Mapped[uuid.UUID]    # Primary key (UUID v7)
    session_id: Mapped[str]
    message_text: Mapped[str]
    received_at: Mapped[datetime]    # UTC; ORDER BY this for sequential processing
    processed: Mapped[bool]          # False → True when queue_consumer_node consumes
    archived: Mapped[bool]           # False → True after 90-day retention window
```

**Indexes**: `(session_id, processed, received_at)` — composite for efficient queue drain.

**Retention Policy**:
- Mark `processed = true` immediately when consumed by `queue_consumer_node` (batch update)
- Nightly job: `UPDATE queued_messages SET archived = true WHERE processed = true AND received_at < NOW() - INTERVAL '90 days'`
- Archived rows are read-only; never hard-deleted

---

## 5. SupportQueue (New Table)

**Purpose**: Escalated sessions awaiting human support agent. Populated by HITL system; consumed by Week 6 UI.

```python
class SupportQueue(Base):
    __tablename__ = "support_queue"
    __table_args__ = {"schema": "agent_v1"}

    queue_id: Mapped[uuid.UUID]       # Primary key (UUID v7)
    session_id: Mapped[str]           # UNIQUE constraint (one active entry per session)
    reason: Mapped[str]               # timeout_60min|rejected_order|max_hitl_exceeded
    created_at: Mapped[datetime]
    assigned_to: Mapped[str | None]   # Support agent ID (Week 6 assigns)
    context_snapshot: Mapped[dict]    # JSONB: {order_details, last_3_messages, rejection_reason}
    status: Mapped[str]               # pending|assigned|resolved|closed
```

**Indexes**: `status + created_at` (admin queue dashboard), `session_id` (UNIQUE for idempotent escalation).

**Idempotency**: UNIQUE on `session_id` prevents duplicate escalation entries (ON CONFLICT DO NOTHING).

---

## 6. InterruptedSession (New Table)

**Purpose**: Tracks active pauses for optimistic locking. One row per session; updated on each pause/resume cycle. **WARNING: Do NOT store `status` here** — `status` is in `HITLMetadata` only. This table tracks escalation_count and version only.

```python
class InterruptedSession(Base):
    __tablename__ = "interrupted_sessions"
    __table_args__ = {"schema": "agent_v1"}

    session_id: Mapped[str]           # Primary key
    next_node: Mapped[str]            # Node name where interrupt() was called
    reason: Mapped[str]               # HITLReasonEnum
    timestamp: Mapped[datetime]       # When current pause started
    version: Mapped[int]              # Optimistic lock; incremented on every approval
    escalation_count: Mapped[int]     # 0–2; from AgentState.hitl_escalation_count
```

**Indexes**: Primary key on `session_id`.

**CRITICAL**: `admin_id` is stored in `HITLMetadata.admin_id`, NOT here. `status` is stored in `HITLMetadata.status`, NOT here. This prevents duplication and synchronization drift per Article I (SSOT).

**Optimistic Locking Protocol**:
1. Client reads `version` via `GET /session/{id}/state`
2. Client sends `expected_version` in `POST /review`
3. Server checks: `WHERE session_id = X AND version = expected_version`
4. If no row updated → `409 Conflict` (stale version, concurrent approval won)
5. If updated → increment `version`, proceed with `update_state()` + resume

---

## 7. Entity Relationship Summary

```
LangGraph checkpoints (4 tables)
    ← thread_id (session_id) →
InterruptedSession (1 per session, optimistic lock)
    ← pause_id →
HITLMetadata (1 per pause instance; multiple per session allowed up to 2)
    ← pause_id →
ReviewAction (N per pause; immutable audit log)

QueuedMessage (N per session; drained on resume)
SupportQueue (1 per escalated session; UNIQUE on session_id)
ConfidenceScore (existing; consumed by hitl_guard_node)
```

---

## 8. Migration Strategy

**Alembic migration**: Create one migration file for all 5 new tables in schema `agent_v1`.

```python
# migrations/versions/XXXX_add_hitl_tables.py
def upgrade():
    op.create_table("hitl_metadata", schema="agent_v1", ...)
    op.create_table("review_actions", schema="agent_v1", ...)
    op.create_table("queued_messages", schema="agent_v1", ...)
    op.create_table("support_queue", schema="agent_v1", ...)
    op.create_table("interrupted_sessions", schema="agent_v1", ...)
    # Indexes
    op.create_index("ix_hitl_metadata_session_id", ...)
    op.create_index("ix_hitl_metadata_status_paused_at", ...)
    op.create_index("ix_queued_messages_session_processed", ...)
    op.create_unique_constraint("uq_support_queue_session_id", ...)
    op.create_unique_constraint("uq_review_actions_idempotency_key", ...)
```

No existing table modifications (additive migration only).
