# Tasks: Human-in-the-Loop (HITL) Control System (Week 4)

**Feature**: `004-human-in-loop-hitl`  
**Input**: `plan.md`, `spec.md`, `data-model.md`, `contracts/hitl-api.yaml`, `research.md`, `quickstart.md`  
**Branch**: `004-human-in-loop-hitl`

## Format: `[ID] [P?] Description`

- **[P]**: Parallelizable — no dependency on other incomplete tasks in this phase
- **Keywords** + **Boilerplate hints** provided per task for developer reference

---

## Phase 1: Setup (Dependencies, Env Vars, Config)

**Purpose**: Install new packages, scaffold directories, add env vars and settings. No business logic yet.  
**⚠️ Must complete before all other phases.**

- [ ] T001 Add `langgraph-checkpoint-postgres` and `psycopg[c]>=3.1.9` to `pyproject.toml` dependencies, then run `uv sync`. The `[c]` extra includes native C extensions for high-performance async (psycopg3's default). If C compilation fails in your environment, the fallback `psycopg[binary]` provides pure Python fallback but with reduced latency. Verify: `uv run python -c "from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver; print('ok')"`. Note: psycopg3 coexists alongside existing `asyncpg` — they serve different purposes (checkpointer vs SQLAlchemy). **Keywords**: `uv add`, `psycopg[c]`, `AsyncPostgresSaver`, `langgraph-checkpoint-postgres`, Article V async

- [ ] T002 [P] Add HITL env vars to `.env.example` (never `.env`): `HITL_TIMEOUT_WARN_MIN=30`, `HITL_TIMEOUT_ESCALATE_MIN=60`, `HITL_MAX_ESCALATION_COUNT=2`, `HITL_PRICE_DELTA_THRESHOLD=0.05`, `HITL_CLASSIFY_CONFIDENCE_THRESHOLD=0.6`, `HITL_COST_THRESHOLD_TOKENS=8000`, `SUPPORT_CONTACT_LINK=https://t.me/support_bot`. **Keywords**: env config, no hardcoded secrets, Article XII

- [ ] T003 Add HITL settings fields to `core/config.py` `Settings` class with Pydantic validation: `HITL_TIMEOUT_WARN_MIN: int = Field(default=30, ge=1)`, `HITL_TIMEOUT_ESCALATE_MIN: int = Field(default=60, ge=2)`, `HITL_MAX_ESCALATION_COUNT: int = Field(default=2, ge=1, le=5)`, `HITL_PRICE_DELTA_THRESHOLD: float = Field(default=0.05, gt=0.0, le=0.5)`, `HITL_CLASSIFY_CONFIDENCE_THRESHOLD: float = Field(default=0.6, ge=0.0, le=1.0)`, `HITL_COST_THRESHOLD_TOKENS: int = Field(default=8000, ge=100)`, `SUPPORT_CONTACT_LINK: str = "https://t.me/support_bot"`. **Keywords**: `pydantic-settings`, `Field`, validation constraints

- [ ] T004 [P] Scaffold new directories: `mkdir -p services/hitl tests/unit tests/integration tests/contract`. Add `__init__.py` to `services/hitl/`, `tests/unit/` (already exists — skip if present). Create empty `services/hitl/__init__.py` with docstring: `"""HITL service layer — gateway, review, timeout, queue management."""`. **Keywords**: package scaffold, `__init__.py`

---

## Phase 2: Data Layer — ORM Models

**Purpose**: Define SQLAlchemy 2.0 async ORM models for all 5 HITL tables. No migration yet — models first, migration second.  
**Prerequisite**: Phase 1 complete. Add all models to `models/schema.py` following existing `Product` / `TextEmbedding` patterns.

- [ ] T005 Add `HITLMetadata` ORM model to `models/schema.py`. Fields: `pause_id: Mapped[uuid.UUID]` (PK, default uuid7), `session_id: Mapped[str]` (indexed), `pause_reason: Mapped[str]`, `paused_at: Mapped[datetime]` (UTC default), `timeout_notified_at: Mapped[datetime | None]`, `escalated_to_support_at: Mapped[datetime | None]`, `status: Mapped[str]` (default `"paused"`), `admin_id: Mapped[str | None]`, `escalation_count: Mapped[int]` (default 0). Schema: `agent_v1`. **Keywords**: `mapped_column`, `DateTime(timezone=True)`, `String`, `SCHEMA`

- [ ] T006 Add `ReviewAction` ORM model to `models/schema.py`. Fields: `action_id: Mapped[uuid.UUID]` (PK uuid7), `session_id: Mapped[str]` (indexed), `pause_id: Mapped[uuid.UUID]`, `action: Mapped[str]` (approve/reject/request_edit), `state_edits: Mapped[dict | None]` (JSONB), `reason_or_comment: Mapped[str | None]`, `timestamp: Mapped[datetime]` (UTC), `admin_user_id: Mapped[str]`, `expected_version: Mapped[int]`, `idempotency_key: Mapped[str]` (UNIQUE constraint — add `UniqueConstraint("idempotency_key")` in `__table_args__`). **Keywords**: `JSONB`, `UniqueConstraint`, `__table_args__`

- [ ] T007 Add `QueuedMessage` ORM model to `models/schema.py`. Fields: `message_id: Mapped[uuid.UUID]` (PK uuid7), `session_id: Mapped[str]` (indexed), `message_text: Mapped[str]` (Text), `received_at: Mapped[datetime]` (UTC, indexed), `processed: Mapped[bool]` (default False), `archived: Mapped[bool]` (default False). Add composite index on `(session_id, processed, received_at)` via `Index("ix_queued_messages_session_proc_time", "session_id", "processed", "received_at")` in `__table_args__`. **Keywords**: `Index`, composite index, queue drain

- [ ] T008 Add `SupportQueue` ORM model to `models/schema.py`. Fields: `queue_id: Mapped[uuid.UUID]` (PK uuid7), `session_id: Mapped[str]` (UNIQUE — `UniqueConstraint("session_id", name="uq_support_queue_session")`), `reason: Mapped[str]`, `created_at: Mapped[datetime]` (UTC), `assigned_to: Mapped[str | None]`, `context_snapshot: Mapped[dict]` (JSONB, default `{}`), `status: Mapped[str]` (default `"pending"`). **Keywords**: `UniqueConstraint`, `JSONB`, SupportQueue idempotency

- [ ] T009 Add `InterruptedSession` ORM model to `models/schema.py`. Fields: `session_id: Mapped[str]` (PK), `next_node: Mapped[str]`, `reason: Mapped[str]`, `timestamp: Mapped[datetime]` (UTC), `admin_id: Mapped[str | None]`, `version: Mapped[int]` (default 0), `escalation_count: Mapped[int]` (default 0). This table has one row per session, upserted on every pause. **Keywords**: text PK, optimistic locking, upsert pattern

---

## Phase 3: Data Layer — Alembic Migration

**Purpose**: One migration file creates all 5 HITL tables. Additive only — no existing table modifications.  
**Prerequisite**: T005–T009 complete (models defined to verify column types).

- [ ] T010 Generate Alembic migration file: `uv run alembic revision --autogenerate -m "add_hitl_tables"`. Then open the generated file and manually verify it contains all 5 tables (`hitl_metadata`, `review_actions`, `queued_messages`, `support_queue`, `interrupted_sessions`) all under schema `agent_v1`. Fix any missing items. **Keywords**: `alembic revision --autogenerate`, `--autogenerate`, review output

- [ ] T011 Add custom indexes to the migration `upgrade()` that autogenerate may miss: `op.create_index("ix_hitl_metadata_status_paused", "hitl_metadata", ["status", "paused_at"], schema="agent_v1")`, `op.create_index("ix_queued_messages_session_proc_time", "queued_messages", ["session_id", "processed", "received_at"], schema="agent_v1")`. Ensure `downgrade()` drops them. **Keywords**: `op.create_index`, `op.drop_index`, migration correctness

- [ ] T012 Apply migration against local DB: `uv run alembic upgrade head`. Verify with `docker compose exec postgres psql -U agent_user -d agent_db -c "\dt agent_v1.*"` — all 5 new tables should appear alongside existing ones. Run `uv run pytest tests/ -x -q` to confirm all 130 existing tests still pass. **Keywords**: smoke test, regression check

- [ ] T081 Create `agent_v1.orders` stub ORM model in `models/schema.py` for order execution (used by T038). Fields: `id: Mapped[uuid.UUID]` (PK uuid7), `session_id: Mapped[str]` (indexed), `customer_id: Mapped[str]` (indexed), `order_info: Mapped[dict]` (JSONB), `status: Mapped[str]` (default `"pending"`), `created_at: Mapped[datetime]` (UTC). Add composite index on `(session_id, created_at)`. Schema: `agent_v1`. **Keywords**: orders table, Article I SSOT (no duplication with HITLMetadata), testability, Phase 2 positioning (not Phase 20.75)

---

## Phase 3: Data Layer — Alembic Migration (Updated)

**Purpose**: Extend `AgentState` with HITL fields and define all Pydantic boundary models.  
**Prerequisite**: T001 complete (langgraph-checkpoint-postgres installed).

- [ ] T013 Add `HITLReasonEnum` to `core/agent/state.py`:
  ```python
  class HITLReasonEnum(StrEnum):
      ORDER_APPROVAL  = "order_approval"
      LOW_CONFIDENCE  = "low_confidence"
      COST_LIMIT      = "cost_limit"
      REFUND_APPROVAL = "refund_approval"
      STALE_PRICE     = "stale_data_price_change"
  ```
  Also add `ORDER_PLACEMENT = "ORDER_PLACEMENT"` to `IntentEnum`. **Keywords**: `StrEnum`, new intent, Article VI

- [ ] T014 Extend `AgentState` TypedDict in `core/agent/state.py` with 8 new fields (add after existing `error` field):
  ```python
  # HITL fields (Week 4)
  hitl_triggered: bool
  hitl_reason: str | None
  hitl_pause_id: str | None
  hitl_rejection_reason: str | None
  hitl_escalation_count: int
  hitl_approved: bool
  estimated_token_cost: int
  order_info: dict | None
  ```
  **Keywords**: `TypedDict`, additive change, state contract

- [ ] T015 Extend `make_initial_state()` in `core/agent/state.py` to include all new HITL fields with safe defaults:
  ```python
  "hitl_triggered": False,
  "hitl_reason": None,
  "hitl_pause_id": None,
  "hitl_rejection_reason": None,
  "hitl_escalation_count": 0,
  "hitl_approved": False,
  "estimated_token_cost": 0,
  "order_info": None,
  ```
  Run `uv run pytest tests/unit/test_agent_state.py -v` to confirm existing state tests still pass. **Keywords**: `make_initial_state`, explicit False defaults, regression

- [ ] T016 Create `services/hitl/schemas.py` with `ApprovalPayload` and `ReviewActionCreate` Pydantic models:
  ```python
  class ReviewActionCreate(BaseModel):
      session_id: str
      pause_id: str
      action: Literal["approve", "reject", "request_edit"]
      expected_version: int
      admin_user_id: str
      state_edits: dict | None = None
      reason_or_comment: str | None = None
      # Validator: state_edits required when action=="request_edit"

  class ApprovalPayload(BaseModel):
      action: Literal["approve", "reject", "request_edit"]
      admin_user_id: str
      state_edits: dict | None = None
      reason_or_comment: str | None = None
      acknowledged_message_ids: list[str] = []
  ```
  **Keywords**: `Literal`, `model_validator`, `field_validator`, Article VI

- [ ] T017 Add `QueueIntentResult` and `QueuedMessageBatch` to `services/hitl/schemas.py`:
  ```python
  class QueueIntentResult(BaseModel):
      message_id: str
      text: str
      intent: Literal["CONFIRM", "CANCEL", "MODIFY_ORDER", "OTHER"]
      confidence: float

  class QueuedMessageBatch(BaseModel):
      session_id: str
      messages: list[QueueIntentResult]
      has_cancel: bool       # True if any intent == CANCEL
      has_modify: bool       # True if any intent == MODIFY_ORDER
      has_confirm: bool      # True if any intent == CONFIRM
  ```
  **Keywords**: batch intent schema, structured output, LiteLLM `response_format`

---

## Phase 5: Checkpointer Factory

**Purpose**: Wrap `AsyncPostgresSaver` in a reusable factory. Isolate psycopg3 setup from the rest of the app (which uses asyncpg).  
**Prerequisite**: T001 complete.

- [ ] T018 Create `core/agent/checkpointer.py`:
  ```python
  """AsyncPostgresSaver factory using psycopg3 (separate from asyncpg).
  Security: JsonPlusSerializer(pickle_fallback=False) prevents CVE-2026-27794.
  """
  from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
  from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
  from psycopg_pool import AsyncConnectionPool

  async def create_checkpointer(dsn: str) -> AsyncPostgresSaver:
      pool = AsyncConnectionPool(conninfo=dsn, max_size=10, open=False)
      await pool.open()
      saver = AsyncPostgresSaver(pool, serde=JsonPlusSerializer(pickle_fallback=False))
      await saver.setup()   # Creates 4 LangGraph tables if not exists
      return saver
  ```
  DSN format: same `DATABASE_URL` from settings but with `postgresql+psycopg` → `postgresql` prefix (psycopg3 uses plain `postgresql://`). **Keywords**: `AsyncConnectionPool`, `JsonPlusSerializer`, `pickle_fallback=False`, pool setup

---

## Phase 6: Node Stubs

**Purpose**: Create all 6 new node files with minimal pass-through stubs so the graph can be wired and imports verified before full implementation.  
**Prerequisite**: T014 complete (AgentState has HITL fields).

- [ ] T019 [P] Create `core/agent/nodes/hitl_guard.py` stub (renamed from hitl_checkpoint for clarity):
  ```python
  """hitl_guard_node — confidence + cost guard; calls interrupt() on threshold breach."""
  from langgraph.types import Command
  from core.agent.state import AgentState

  async def hitl_guard_node(state: AgentState) -> Command:
      """Stub: pass-through until T025."""
      return Command(goto="answer_node")
  ```
  **Keywords**: node stub, confidence guard, cost guard

- [ ] T020 [P] Create stubs for `core/agent/nodes/queue_consumer.py`, `core/agent/nodes/state_freshness.py`, `core/agent/nodes/order_execution.py`. Each follows the same pattern: async function returning `Command(goto="answer_node")` (temporary). **Keywords**: parallel stub creation, node pattern

- [ ] T021 [P] Create stubs for `core/agent/nodes/cancellation.py` and `core/agent/nodes/customer_support.py`. Both return `Command(goto="answer_node")` temporarily. **Keywords**: parallel stub creation

---

## Phase 7: Graph Wiring

**Purpose**: Register all new nodes in `graph.py`, add edges, integrate `AsyncPostgresSaver`.  
**Prerequisite**: T018 (checkpointer), T019–T021 (stubs).

- [ ] T022 Update `core/agent/graph.py` — add imports for all 6 new nodes and add them to `GRAPH_NODES` set:
  ```python
  from core.agent.nodes.hitl_guard import hitl_guard_node
  from core.agent.nodes.queue_consumer import queue_consumer_node
  from core.agent.nodes.state_freshness import state_freshness_validator_node
  from core.agent.nodes.order_execution import order_execution_node
  from core.agent.nodes.cancellation import cancellation_node
  from core.agent.nodes.customer_support import customer_support_node
  ```
  Add all 6 to `GRAPH_NODES`. **Keywords**: import, GRAPH_NODES set update

- [ ] T023 Register all 6 new nodes in `build_graph()` with `builder.add_node(...)`. Then add edges:
  - `confidence_node` conditional → `hitl_guard_node`, `answer_node` (if high confidence, skip guard)
  - `hitl_guard_node` conditional → `answer_node` (OK), interrupts on low confidence/cost
  - `queue_consumer_node` conditional edges → `state_freshness_validator_node`, `cancellation_node`, `hitl_guard_node`
  - `state_freshness_validator_node` conditional → `order_execution_node`, `customer_support_node`, `hitl_guard_node`
  - `order_execution_node` → `answer_node`
  - `cancellation_node` → `answer_node`
  - `customer_support_node` → `END`
  **Keywords**: `add_node`, `add_conditional_edges`, `add_edge`, graph topology

- [ ] T024 Update `build_graph()` signature to accept `checkpointer=None` and update `astream_agent()` to accept + pass `checkpointer`. Also add `_route_after_confidence()` function to route confidence_node output to `hitl_guard_node` when confidence < 0.7, or to `answer_node` when confidence OK. Verify graph compiles: `uv run python -c "from core.agent.graph import build_graph; g = build_graph(); print(g.get_graph().draw_mermaid())"`. **Keywords**: compile check, conditional routing, Mermaid output, regression

---

## Phase 8: Implement `hitl_guard_node`

**Purpose**: Confidence + cost guard. Fires `interrupt()` only when thresholds breached (adaptive).  
**Prerequisite**: T022–T024, T005, T009 (ORM models loaded).

- [ ] T025 Implement the **confidence check** in `hitl_guard_node`: if `state["confidence_score"] < settings.HITL_CONFIDENCE_THRESHOLD` (default 0.7): generate `pause_id = str(uuid7())`, insert `HITLMetadata` and upsert `InterruptedSession`. Check `state["hitl_escalation_count"] >= settings.HITL_MAX_ESCALATION_COUNT` — if True, route directly to `customer_support_node` (skip interrupt). Otherwise call `interrupt({"reason": "low_confidence", ...})`. **Keywords**: confidence threshold (0.7), FR-005, FR-015 overflow guard

- [ ] T026 Implement the **cost check** in `hitl_guard_node`: call `litellm.token_counter(model=state["model_used"], messages=state["messages"])` to estimate compressed context tokens. If `estimated > settings.HITL_COST_THRESHOLD_TOKENS` (default 8000): same pause logic as confidence check. Reason: `"cost_limit_exceeded"`. **Keywords**: `litellm.token_counter`, FR-006, adaptive guard

- [ ] T027 Implement the **resume handler** in `hitl_guard_node` (code after `interrupt()` returns): extract `approval_payload = ApprovalPayload.model_validate(interrupt_result)`. If `action == "approve"`: return `Command(goto="queue_consumer_node", update={"hitl_approved": True, "hitl_triggered": False})`. **Keywords**: `ApprovalPayload.model_validate`, resume routing, `Command(goto=...)`

- [ ] T028 Implement the **rejection handler** in resume path: if `action == "reject"`: set `hitl_rejection_reason = payload.reason_or_comment`, increment `hitl_escalation_count`, update `HITLMetadata.status = "rejected"`, return `Command(goto="customer_support_node", update={...})`. **Keywords**: rejection routing, `HITLMetadata` status update, escalation_count increment

---

## Phase 9: Implement `queue_consumer_node`

**Purpose**: Post-resume message processing, orphan tool cleanup, intent classification, routing.  
**Prerequisite**: T025–T028, T007 (QueuedMessage model), T016–T017 (schemas).  
**Critical**: All QueuedMessage state mutations MUST be wrapped in a single database transaction (FR-029). If transaction fails, all mutations revert for safe retry.

- [ ] T029 Implement **orphan tool call scanner**: scan only the most recent 10–20 AIMessages (limit: 10–20 messages) to avoid performance degradation on long conversations (FR-025). Collect all `ToolCall` IDs from AIMessages in the limited window. Then collect all `ToolMessage.tool_call_id` values in the full history. For each unmatched ToolCall ID, append a synthetic `ToolMessage(tool_call_id=id, content="[cancelled: session resumed]")` to `state["messages"]`. Return updated messages. This prevents LLM API errors on orphan tool calls (spec Edge Case 2). **Keywords**: `AIMessage`, `ToolMessage`, `tool_call_id`, orphan scan, `add_messages`, performance limit, FR-025

- [ ] T030 Implement **QueuedMessage drain with transaction boundary**: Begin atomic SQLAlchemy transaction here (not in T029–T034). Query DB for `WHERE session_id = X AND processed = False ORDER BY received_at ASC` (limit 20). For each, append `HumanMessage(content=f"[Customer follow-up during review]: {msg.message_text}")` to messages. At end of routing decision (T034), batch-update `processed = True` in single UPDATE statement within same transaction. Store drained message IDs for rollback handling. **Keywords**: async SQLAlchemy `select`, `update().where()`, transaction context, batch update, `HumanMessage`, FR-029

- [ ] T031 Implement **batch intent classification** with threshold: call `LiteLLM` economy model with `response_format=QueuedMessageBatch` to classify each message intent (`CONFIRM/CANCEL/MODIFY_ORDER/OTHER`). If classifier returns `confidence < 0.6` on the net batch intent, default conservatively to `has_confirm=True` (FOLLOW_UP path, not CANCEL or re-pause). This prevents ambiguous classification from blocking orders (FR-024, spec Edge Case). **Keywords**: `litellm.acompletion`, economy model, `response_format`, confidence threshold check (0.6), FR-024

- [ ] T032 Implement **CANCEL override routing** (highest priority): if `batch.has_cancel == True` → return `Command(goto="cancellation_node", update={"hitl_approved": False})`. CANCEL always overrides admin approval. No check on admin decision (spec FR-023, Edge Case 4). **Keywords**: CANCEL override, priority routing, FR-023

- [ ] T033 Implement **MODIFY_ORDER re-pause**: if `batch.has_modify == True` AND queued modification differs from `state["order_info"]` (compare fields): increment `hitl_escalation_count` by 1, then return `Command(goto="hitl_guard_node", update={"hitl_escalation_count": new_count, "hitl_triggered": False, "hitl_pause_id": None})`. The `hitl_guard_node` will issue a new `interrupt()`. Do NOT re-apply admin's previous state_edits (they are stale — spec Edge Case 1). **Keywords**: MODIFY_ORDER re-pause, Double Correction fix, increment escalation_count

- [ ] T034 Implement **CONFIRM/OTHER fallthrough**: if no CANCEL and no MODIFY → return `Command(goto="state_freshness_validator_node")`. **Keywords**: fallthrough routing, default path

---

## Phase 10: Implement `state_freshness_validator_node`

**Purpose**: Re-verify inventory and price against DB before executing the order (stale data guard).  
**Prerequisite**: T009, T029, T034.

- [ ] T035 Query `products` table for `state["order_info"]["product_id"]`: fetch current `stock_quantity` and `price`. If `stock_quantity <= 0` → return `Command(goto="customer_support_node", update={"hitl_rejection_reason": "out_of_stock"})`. **Keywords**: async SELECT, stock check, FR-027

- [ ] T036 Compute price delta: `delta = abs(current_price - approved_price) / approved_price`. If `delta >= settings.HITL_PRICE_DELTA_THRESHOLD` (default 0.05 = 5%): update `state["order_info"]["approved_price"]` to `current_price` so admin sees the new price, then return `Command(goto="hitl_guard_node", update={"hitl_triggered": False, "hitl_pause_id": None, "hitl_reason": HITLReasonEnum.STALE_PRICE})`. Note: stale_price re-pause does NOT increment `escalation_count` (spec Edge Case 3). **Keywords**: price delta, `HITL_PRICE_DELTA_THRESHOLD`, stale_price re-pause, no escalation_count increment

- [ ] T037 Implement freshness-ok path: if stock > 0 and delta < threshold → return `Command(goto="order_execution_node", update={"hitl_freshness_valid": True})`. Also add `hitl_freshness_valid: bool` field to `AgentState` and `make_initial_state()` (default `False`). **Keywords**: freshness-ok routing, state field addition

---

## Phase 11: Implement `order_execution_node`

**Purpose**: Actual order placement — deduct stock, record order.  
**Prerequisite**: T035–T037.

- [ ] T038 Implement order placement: within a single DB transaction — (1) decrement `products.stock_quantity` by `order_info["quantity"]` (with `WHERE stock_quantity >= quantity` guard to prevent negative stock), (2) insert an order record into `agent_v1.orders` table (created by T081; use structure: id, session_id, customer_id, order_info, status, created_at). Return `Command(goto="answer_node", update={"response": confirmation_message, "order_info": {**order_info, "status": "confirmed"}})`. **Keywords**: DB transaction, stock decrement, order record, confirmation message, T081 orders table. **Note**: Does NOT use session metadata_; orders are persisted to dedicated table for auditability and Phase 2/3 SME scaling.

---

## Phase 12: Implement `cancellation_node`

**Prerequisite**: T032.

- [ ] T039 Implement cancellation: if `state["order_info"]` exists, update its `status = "cancelled"` in DB (or state). Compose a polite cancellation message: `"Your order has been cancelled as requested. [support_link]"`. Return `Command(goto="answer_node", update={"response": message, "order_info": {**order_info, "status": "cancelled"}})`. **Keywords**: cancellation message, answer_node routing

---

## Phase 13: Implement `customer_support_node`

**Prerequisite**: T028, T035, T038.

- [ ] T040 Implement empathetic message via LiteLLM economy model: compose message using `state["hitl_rejection_reason"]` as context. Example system prompt: `"You are a compassionate sales assistant. The customer's request could not be processed. Compose a brief, empathetic response explaining {reason} and directing them to {support_link}."`. Use `response_format=None` (plain text response). **Keywords**: LiteLLM economy model, empathetic message, `settings.SUPPORT_CONTACT_LINK`

- [ ] T041 Insert into `SupportQueue` table: `ON CONFLICT (session_id) DO NOTHING` (idempotent). Set `context_snapshot` to `{"order_info": state["order_info"], "rejection_reason": state["hitl_rejection_reason"], "last_messages": state["messages"][-3:]}`. Update `HITLMetadata.status = "escalated"`. Return `Command(goto=END, update={"response": empathetic_message})`. **Keywords**: `SupportQueue`, `ON CONFLICT DO NOTHING`, idempotent escalation, `END`

---

## Phase 14: HITLService Layer

**Purpose**: Service class used by the `/review` API route. Handles gateway check, state reads, review processing, idempotency, and message enqueueing.  
**Prerequisite**: T005–T009 (ORM), T016–T017 (schemas), T018 (checkpointer).

- [ ] T042 Create `services/hitl/service.py` with `HITLService` class. Implement `get_session_state(session_id, graph, config) -> dict`: call `await graph.aget_state(config)` to get `StateSnapshot`. Extract `values`, `next` nodes, `tasks`. Also query `InterruptedSession` and `HITLMetadata` for operational metadata. Return combined dict. **Keywords**: `aget_state`, `StateSnapshot`, `.values`, `.next`, `.tasks`

- [ ] T043 Implement `HITLService.enqueue_message(session_id: str, message_text: str, db) -> None`: insert `QueuedMessage` row with `processed=False`. This is called by the Paused Session Gateway when a new customer message arrives for a paused session. **Keywords**: `QueuedMessage` insert, async DB, gateway integration

- [ ] T044 Implement `HITLService.process_approve(payload: ReviewActionCreate, db, graph, config) -> dict` with **FR-031 resuming state failure recovery**: (1) Check idempotency: `SELECT action_id FROM review_actions WHERE idempotency_key = X` → if found return cached `{"status": "hit", "action_id": existing_id}`. (2) Optimistic lock: `UPDATE interrupted_sessions SET version = version+1 WHERE session_id = X AND version = expected_version` → if 0 rows updated raise `409 Conflict`. (3) Insert `ReviewAction` row. (4) If `state_edits` exist: call `graph.update_state(config, state_edits, as_node="hitl_review_node")` first. (5) **Set status="resuming"** immediately before invoking graph: `UPDATE hitl_metadata SET status = "resuming" WHERE pause_id = X`. (6) **Resume with exception handling**: `try: await graph.ainvoke(Command(resume=ApprovalPayload(...).model_dump()), config=config) except Exception as e: UPDATE hitl_metadata SET status = "paused"; log error; raise 500`. (7) On success: `UPDATE HITLMetadata.status = "approved"`. **Keywords**: idempotency_key, optimistic lock, resuming state, exception catch/revert, FR-031, Pattern B, failure recovery

- [ ] T045 Implement `HITLService.process_reject(payload: ReviewActionCreate, db, graph, config) -> dict`: same idempotency + optimistic lock as T044. Then: insert `ReviewAction`, resume with `action="reject"`, update `HITLMetadata.status = "rejected"`. **Keywords**: reject path, same lock pattern

- [ ] T046 Implement `HITLService.process_request_edit()` with **structured synthetic message** (Article VI compliance) AND **downstream schema validation** (FR-024 edge case fix): Pattern B: (1) **Validate edits against downstream schema**: for each field in `state_edits`, check if it matches the schema expected by the node that will consume it (e.g., if editing `order_info.size`, verify size ∈ allowed sizes per product; if editing `price_override`, verify it's numeric and within ±10% of catalog). Use `HITLMetadata.pending_node` to determine the consumer node and load its schema from ORM model. Return `422` if validation fails. (2) For each valid field, build a structured dict **with pause_id** for message replacement (FR-028):
  ```python
  synthetic = {
      "type": "admin_override",
      "pause_id": str(hitl_metadata.pause_id),  # For FR-028 message replacement
      "field": field_name,
      "old_value": current_state[field],
      "new_value": new_value,
      "admin_id": payload.admin_user_id,
      "timestamp": datetime.now(UTC).isoformat(),
      "reason": payload.reason_or_comment or ""
  }
  ```
  (3) Find customer's **last HumanMessage** in messages list (temporal order). (4) Insert synthetic message immediately **after** customer's last message (not at tail). (5) Call `graph.update_state(config, {...}, as_node="hitl_review_node")` with the synthetic message. (6) Insert `ReviewAction` with `action="request_edit"` and `acknowledged_message_ids` populated. Return `{"status": "edited", "action_id": ...}` — do NOT resume yet (admin must call approve separately). **Keywords**: Pattern B, structured dict, pause_id for message replacement, downstream schema validation, message insertion position (after customer), no auto-resume, Article VI compliance, Double Correction fix, schema mismatch prevention, acknowledged_message_ids

---

## Phase 15: Timeout Scheduler

**Purpose**: Background `asyncio` task running in FastAPI lifespan. Polls for timed-out sessions.  
**Prerequisite**: T005, T008, T042.

- [ ] T047 Create `services/hitl/timeout_scheduler.py`. Implement `async def run_timeout_scheduler(db_session_factory, poll_interval_seconds=60)` — an infinite async loop that sleeps `poll_interval_seconds` between runs. On each iteration: query `HITLMetadata WHERE status='paused' AND paused_at < NOW() - {warn_min} minutes AND timeout_notified_at IS NULL`. **Keywords**: `asyncio.sleep`, infinite loop, lifespan task, DB query

- [ ] T048 Implement 30-min warn path in scheduler: for each session found in T047 query, log a structured warning (or send Telegram message via `settings.SUPPORT_CONTACT_LINK`), then `UPDATE hitl_metadata SET timeout_notified_at = NOW() WHERE pause_id = X`. Structured log: `{"event": "hitl_timeout_warn", "session_id": X, "paused_at": T}`. **Keywords**: structured logging, `timeout_notified_at`, FR-016

- [ ] T049 Implement 60-min escalation path in same scheduler loop: query `HITLMetadata WHERE status='paused' AND paused_at < NOW() - {escalate_min} minutes`. For each: call `HITLService.escalate_to_support(session_id, db)` which writes `SupportQueue` (ON CONFLICT DO NOTHING) and updates `HITLMetadata.status = "escalated"`. Log: `{"event": "hitl_timeout_escalate", "session_id": X}`. **Keywords**: 60-min escalation, SupportQueue, FR-016, spec Edge Case

---

## Phase 16: API Routes

**Purpose**: Expose HITL operations as REST endpoints. Secured by existing `verify_admin_key` dependency.  
**Prerequisite**: T042–T046, T016–T017.

- [ ] T050 Create `api/routes/hitl.py` with `APIRouter(prefix="/hitl", tags=["hitl"])`. Implement `GET /hitl/session/{session_id}/state` endpoint: call `HITLService.get_session_state()`, return `StateSnapshot` dict. Dependency: `Depends(verify_admin_key)`. Return 404 if session not found in `InterruptedSession`. **Keywords**: `APIRouter`, `verify_admin_key`, `Depends`, GET state endpoint

- [ ] T051 Implement `POST /hitl/review` endpoint in `api/routes/hitl.py`: extract `X-Idempotency-Key` header (`idempotency_key: str = Header(..., alias="X-Idempotency-Key")`). Parse body as `ReviewActionCreate`. Route to correct `HITLService` method based on `payload.action`. Return structured response with `action_id` and `status`. **Keywords**: `Header`, `alias`, `ReviewActionCreate`, action routing, `X-Idempotency-Key`

- [ ] T052 Add explicit HTTP error handling in `hitl.py` POST /review handler: (1) **Terminal status gate** (FR-033, SC-035): before processing any action (approve/reject/edit), query `HITLMetadata.status` — if status ∈ ["approved", "rejected", "abandoned", "escalated"], return `HTTPException(409, {"error": "Session already resolved", "status": session.status, "assigned_to": hitl_metadata.admin_id})` (FR-027 requires assigned_to in 409 response). (2) Catch `409` (optimistic lock from update_state) → `HTTPException(409, "Version conflict — reload and retry")`. (3) Catch `404` (session not paused) → `HTTPException(404, "No active HITL pause for session")`. (4) Catch Pydantic `ValidationError` → `HTTPException(422, detail=str(e))`. **Keywords**: `HTTPException`, error codes, 409/404/422, terminal status, session lock, assigned_to field, HITLMetadata (not InterruptedSession)

---

## Phase 17: FastAPI Integration

**Purpose**: Mount HITL router, start checkpointer in lifespan, add Paused Session Gateway.  
**Prerequisite**: T018 (checkpointer factory), T050–T052 (routes).

- [ ] T053 Update `api/main.py` lifespan: add `checkpointer = await create_checkpointer(settings.DATABASE_URL_PSYCOPG)` on startup (use a separate `DATABASE_URL_PSYCOPG` settings field that replaces `postgresql+asyncpg://` with `postgresql://`). Store in `app.state.checkpointer`. On shutdown: `await app.state.checkpointer.conn.close()` (or pool close). **Keywords**: `app.state`, lifespan, psycopg3 DSN, pool lifecycle

- [ ] T054 Add `DATABASE_URL_PSYCOPG` to `core/config.py`: derive from `DATABASE_URL` by replacing `asyncpg` driver with psycopg3 driver (`postgresql+asyncpg://` → `postgresql://`). Use `@computed_field` or a `@property`. Add to `.env.example` as comment. **Keywords**: `@computed_field`, DSN transformation, psycopg3 URL format

- [ ] T055 Mount hitl router in `api/main.py`: `app.include_router(hitl_router)`. Add `from api.routes.hitl import router as hitl_router`. Verify routes appear: `uv run python -c "from api.main import app; print([r.path for r in app.routes])"`. **Keywords**: `include_router`, route registration

- [ ] T056 Implement **Paused Session Gateway** as a FastAPI dependency `check_paused_session(session_id: str, message: str, db, request: Request)`: query `HITLMetadata WHERE session_id=X AND status='paused'`. If paused → call `HITLService.enqueue_message(session_id, message, db)`, return `{"queued": True, "message": "Your message has been received. An agent is reviewing your request."}` immediately (skip graph). If not paused → return `{"queued": False}` and let normal routing proceed. Apply this dependency to the main `/agent/query` endpoint in `api/routes/agent.py`. **Keywords**: `Depends`, gateway dependency, enqueue on pause, FR-024, spec §Architecture Layer 1

- [ ] T057 Update `router_node` in `core/agent/nodes/router.py`: add `ORDER_PLACEMENT` to the system prompt intent list and to `_get_next_node()` routing logic — `ORDER_PLACEMENT → "hitl_guard_node"`. **Keywords**: router_node extension, ORDER_PLACEMENT intent routing

---

## Phase 18: Unit Tests

**Purpose**: Isolated tests for each HITL node and service method. No DB required — use mocks.  
**Prerequisite**: Phase 8–16 complete.

- [ ] T058 Create `tests/unit/test_hitl_guard_node.py`. Test: (1) **pause fires** — when `escalation_count < max`, `interrupt()` is called (mock `interrupt` to capture payload), HITLMetadata and InterruptedSession are written. (2) **overflow guard** — when `escalation_count >= max`, node routes directly to `customer_support_node` without calling `interrupt()`. Use `pytest-asyncio`, mock db session. **Keywords**: mock `interrupt`, overflow guard, `asyncio_mode=strict`

- [ ] T059 Add tests to `test_hitl_guard_node.py`: (3) **approve resume** — when `interrupt()` returns `{"action":"approve"}`, node returns `Command(goto="queue_consumer_node")`. (4) **reject resume** — returns `Command(goto="customer_support_node")` with `hitl_rejection_reason` set. **Keywords**: resume path, approve/reject routing

- [ ] T060 Create `tests/unit/test_queue_consumer_node.py`. Test: (1) **orphan ToolCall** — state with AIMessage containing `tool_calls=[{id:"call_abc"}]` and no ToolMessage → after node, messages contain synthetic `ToolMessage(tool_call_id="call_abc", content="[cancelled: session resumed]")`. (2) **empty queue** — no QueuedMessages → routes to `state_freshness_validator_node` directly. **Keywords**: orphan tool call fix, ToolMessage injection, empty queue path

- [ ] T061 Add tests to `test_queue_consumer_node.py`: (3) **CANCEL override** — QueuedMessage classified as CANCEL → routes to `cancellation_node` even when `hitl_approved=True`. (4) **MODIFY_ORDER re-pause** — MODIFY classified with changed `order_info` → `escalation_count` incremented, routes to `hitl_guard_node`. (5) **Double Correction guard** — admin previously set size=L; MODIFY queue message also sets size=L (no change) → treated as CONFIRM, not re-pause. **Keywords**: CANCEL override, Double Correction fix, MODIFY_ORDER re-pause

- [ ] T062 Create `tests/unit/test_state_freshness_node.py`. Tests: (1) **out-of-stock** — product `stock_quantity=0` → routes to `customer_support_node` with `rejection_reason="out_of_stock"`. (2) **price delta > 5%** — `current_price=105`, `approved_price=100` → routes to `hitl_guard_node`, `escalation_count` unchanged (not incremented). (3) **freshness ok** — stock > 0 and delta < 5% → routes to `order_execution_node`, `hitl_freshness_valid=True`. **Keywords**: price delta, out-of-stock, no escalation_count on stale_price

- [ ] T063 Create `tests/unit/test_hitl_service.py`. Tests: (1) **idempotency replay** — `process_approve()` called twice with same `idempotency_key` → second call returns `{"status":"hit"}`, no duplicate DB writes, no second graph resume. (2) **optimistic lock conflict** — `expected_version=0` but `InterruptedSession.version=1` → raises `HTTPException(409)`. (3) **request_edit no auto-resume** — `process_request_edit()` updates state but does NOT call `ainvoke(Command(resume=...))`. **Keywords**: idempotency, 409 conflict, Pattern B no-auto-resume

- [ ] T064 Create `tests/unit/test_hitl_schemas.py`. Tests: (1) **ReviewActionCreate validation** — `action="request_edit"` with `state_edits=None` raises `ValidationError`. (2) **ApprovalPayload round-trip** — serialize/deserialize via `model_dump()` / `model_validate()`. (3) **QueuedMessageBatch computed fields** — `has_cancel=True` when any message has `intent="CANCEL"`. **Keywords**: Pydantic `model_validator`, `ValidationError`, schema contract

---

## Phase 19: Integration Tests

**Purpose**: End-to-end flow tests using real LangGraph graph with in-memory checkpointer (or test Postgres).  
**Prerequisite**: All Phase 8–17 tasks complete.

- [ ] T065 Create `tests/integration/test_hitl_flow.py`. Test: **happy path approve → execute** — (1) invoke graph with ORDER_PLACEMENT message, (2) graph pauses at `hitl_guard_node`, (3) confirm `graph.get_state(config).next == ["hitl_guard_node"]`, (4) simulate admin `process_approve()`, (5) graph resumes, processes empty queue, validates freshness, executes order. Assert final state has `order_info["status"] == "confirmed"`. Use `MemorySaver` checkpointer for speed. **Keywords**: `MemorySaver`, `get_state`, `Command(resume=...)`, end-to-end, happy path

- [ ] T066 Add test: **reject → customer_support → support_queue** — pause graph, simulate `process_reject()`, assert graph routes to `customer_support_node`, `SupportQueue` row inserted, `HITLMetadata.status == "rejected"`. **Keywords**: rejection flow, SupportQueue, HITLMetadata status

- [ ] T067 Add test: **CANCEL during pause** — graph paused, customer sends CANCEL message (enqueued), admin approves, resume: `queue_consumer_node` classifies CANCEL, routes to `cancellation_node` overriding admin approval. Assert `order_info["status"] == "cancelled"`. **Keywords**: CANCEL override integration, post-approve routing

---

## Phase 20: Contract Tests

**Purpose**: FastAPI TestClient tests for `/hitl` endpoints.  
**Prerequisite**: T050–T057 complete.

- [ ] T068 Create `tests/contract/test_hitl_api.py`. Test: (1) `GET /hitl/session/{id}/state` without `X-Admin-Key` → 403. (2) `GET /hitl/session/{id}/state` with valid key but no pause → 404. (3) `POST /hitl/review` with valid body and `X-Idempotency-Key` → 200 with `action_id`. **Keywords**: `TestClient`, 403/404/200, header validation

- [ ] T069 Add idempotency contract test: send identical `POST /hitl/review` twice with same `X-Idempotency-Key` → second response has `"status": "hit"`, identical `action_id`. Add version-conflict test: `expected_version=999` → 409. **Keywords**: idempotency replay, 409 optimistic lock, contract level

---

## Phase 20: Background Tasks & Data Maintenance

**Purpose**: Implement nightly maintenance and async services not covered in previous phases.  
**Prerequisite**: T005–T012 (DB schema complete), T042–T049 (services defined).

- [ ] T070 Create `services/hitl/archive_scheduler.py`. Implement `async def run_nightly_archive(db_session_factory, batch_size=1000)`: query `queued_messages WHERE processed=True AND received_at < NOW() - INTERVAL '90 days' AND archived=False` in batches. Update each batch `SET archived=True`. Log: `{"event": "nightly_archive", "count": batch_size, "timestamp": NOW()}`. This runs once per 24 hours via FastAPI lifespan. **Keywords**: nightly job, QueuedMessage retention, archived flag, FR-021, 90-day policy

- [ ] T071 Create `services/hitl/telegram_service.py`. Implement `async def send_telegram_message(chat_id: str, message_text: str) → bool`: wrapper around Telegram Bot API. Inject `settings.TELEGRAM_BOT_TOKEN` (from env). Return True on success, False on failure. Handle rate-limit 429 with exponential backoff. **Keywords**: Telegram service, Bot API, settings injection, send_telegram_message function

- [ ] T072 Implement `compressed_context` transformation in `services/hitl/cost_guard.py` (used by T026 confidence/cost guards): extract last 5 messages from conversation history + current user intent + product name. Tokenize and estimate cost using `num_tokens ≈ len(text) / 4`. Document the heuristic. **Keywords**: compressed context, token estimation, cost guard input, T026 dependency

- [ ] T073 Add Telegram service dependency to FastAPI lifespan: inject `TelegramService` into timeout_scheduler and support_queue routes. Verify service is available before starting scheduler loop. **Keywords**: lifespan integration, dependency injection, Telegram availability check

---

## Phase 20.5: Latency & Performance Verification

**Purpose**: Verify Article V strict async compliance and performance goals.  
**Prerequisite**: T051–T057 (all endpoints complete), T070–T073 (background tasks).

- [ ] T074 Create `tests/performance/test_hitl_latency.py`. Implement **endpoint latency test**: invoke `POST /hitl/review` with valid payload 10 times and measure response time. Assert p95 < 200ms (spec requirement). Use `pytest-benchmark` or simple `time.time()` measurement. **Keywords**: p95 latency, 200ms target, performance regression gate

- [ ] T075 Create latency test for **queue_consumer_node batch classification**: mock LiteLLM completion, measure time from node entry to exit with 5 queued messages. Assert < 500ms (spec requirement). Use `asyncio.get_event_loop().time()` for precise timing. **Keywords**: queue classification latency, 500ms target, async timing

- [ ] T076 Add **async safety check** to ruff: run `uv run ruff check --select ASYNC .` after all tasks complete. Verify no blocking calls in `hitl.py`, `hitl_guard.py`, `queue_consumer.py`, `timeout_scheduler.py`, `archive_scheduler.py`. Article V compliance gate. **Keywords**: `ASYNC` linter rule, no blocking I/O, event loop safety

---

## Phase 20.75: Legacy — REMOVED

**Purpose**: (LEGACY — All moved to earlier phases)  
- T081 moved to Phase 2 (orders table definition)
- Other tasks remain in their phases

---

## Phase 21: Final Verification

**Purpose**: Run full test suite, verify graph topology, check ruff linting.

- [ ] T077 Run full test suite: `uv run pytest tests/ -v --tb=short 2>&1 | tail -30`. All existing 130+ tests must pass, plus all new HITL tests. Target: 0 failures. If failures exist, fix before proceeding. **Keywords**: regression gate, full suite, zero failures

- [ ] T078 Run ruff lint + format check: `uv run ruff check . && uv run ruff format --check .`. Fix any issues: `uv run ruff check --fix . && uv run ruff format .`. Pay special attention to `ASYNC` rules (no blocking I/O in async functions) and `TCH` (type-checking imports). **Keywords**: `ruff check`, `ruff format`, ASYNC safety, Article V

- [ ] T079 Export updated Mermaid graph diagram: `uv run python -c "from core.agent.graph import export_mermaid_to_file; export_mermaid_to_file('docs/week4/agent-graph.mmd')"`. Verify all 11 nodes appear: router, retrieval, confidence, **hitl_guard** (new), queue_consumer, state_freshness, order_execution, cancellation, customer_support, answer, END. Commit to `docs/week4/agent-graph.mmd`. **Keywords**: Mermaid export, graph documentation, 11 nodes, node naming

- [ ] T080 Write `docs/week4/week4.md` (create if absent) with: 6 new node descriptions, HITL flow summary, new `AgentState` fields, new API endpoints, migration notes. Include section on **structured synthetic messages** (Article VI) and **dynamic interrupt() guards** (adaptive logic that checks confidence < 0.7 and cost > 8000 tokens before escalating to HITL). Explain why dynamic `interrupt()` inside `hitl_guard_node` is preferred over static `interrupt_before` (routing flexibility to `queue_consumer_node`). This is the developer reference for future weeks. **Keywords**: developer documentation, week4.md, handoff doc, Article VI compliance, dynamic interrupt rationale

---

## Dependency Map

```
T001 → T018 → T022 → T023 → T024
T002 → T003
T005–T009 → T010 → T011 → T012
T013–T015 → T016–T017
T014 → T019–T021 → T022
T025–T028 require T022–T024, T005, T009
T029–T034 require T025–T028, T007
T035–T037 require T029–T034
T038 requires T035–T037
T039 requires T032
T040–T041 require T028, T035
T042–T046 require T005–T009, T016–T017, T018
T047–T049 require T042
T050–T052 require T042–T046
T053–T057 require T018, T050–T052
T058–T064 (unit) require Phase 8–16 stubs + implementations
T065–T067 (integration) require all nodes complete
T068–T069 (contract) require T050–T057
T070–T073 (background tasks) require T005–T012, T042–T049
T074–T076 (performance) require T057, T034, T070–T071
T077–T080 (final) require T070–T073 (T081 now in Phase 2, not a dependency for later phases)
T081 (orders table) requires T005–T012 (moved to Phase 2; no longer blocking Phase 20.75)
```

---

## Parallelization Opportunities

| Parallel Group | Tasks |
|---|---|
| After T001 | T002, T004 |
| DB models | T005, T006, T007, T008, T009 (one dev per model) |
| Schemas | T013, T016, T017 (independent files) |
| Node stubs | T019, T020, T021 (parallel file creation) |
| Later nodes | T038, T039, T040–T041 (independent nodes) |
| Unit tests | T058–T064 (independent test files) |
