# Quickstart: HITL Feature Development

**Branch**: `004-human-in-loop-hitl` | Week 4

---

## Prerequisites

- Week 3 agent running: `uv run python -m core.agent` works offline
- PostgreSQL 17 + pgvector running: `docker compose up postgres`
- LangGraph PostgresSaver configured with `AsyncPostgresSaver`

---

## 1. Run Migration

```bash
uv run alembic upgrade head
```

Applies migration: `XXXX_add_hitl_tables.py` — creates 5 new tables in `agent_v1` schema.

Verify:
```bash
docker compose exec postgres psql -U agent_user -d agent_db \
  -c "\dt agent_v1.*" | grep -E "hitl|queued|support|interrupted|review"
```

Expected output:
```
 agent_v1 | hitl_metadata       | table
 agent_v1 | interrupted_sessions| table
 agent_v1 | queued_messages     | table
 agent_v1 | review_actions      | table
 agent_v1 | support_queue       | table
```

---

## 2. Run Tests (TDD Baseline)

```bash
# Unit tests (confidence/cost guard logic)
uv run pytest tests/unit/test_hitl_guard.py -v

# Contract tests (API endpoints)
uv run pytest tests/contract/test_hitl_api.py -v

# Integration test (end-to-end pause → approve → resume)
uv run pytest tests/integration/test_hitl_flow.py -v
```

All tests must pass before moving to implementation phase.

---

## 3. Trigger HITL Manually (Dev Testing)

Start the agent:
```bash
uv run uvicorn api.main:app --reload --port 8000
```

Send a low-confidence query (forces HITL):
```bash
curl -X POST http://localhost:8000/agent/query \
  -H "Content-Type: application/json" \
  -d '{"session_id": "test-001", "message": "What is the absolute cheapest product?"}'
```

Check if session is paused:
```bash
curl http://localhost:8000/hitl/session/test-001/state \
  -H "X-Admin-Key: dev-admin-key"
```

Approve the paused session:
```bash
curl -X POST http://localhost:8000/hitl/review \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: dev-admin-key" \
  -H "X-Idempotency-Key: $(uuidgen)" \
  -d '{
    "session_id": "test-001",
    "pause_id": "<pause_id_from_state_response>",
    "action": "approve",
    "expected_version": 0,
    "admin_user_id": "admin-dev"
  }'
```

---

## 4. Test Edit Flow

Approve with state edit (price correction):
```bash
curl -X POST http://localhost:8000/hitl/review \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: dev-admin-key" \
  -H "X-Idempotency-Key: $(uuidgen)" \
  -d '{
    "session_id": "test-001",
    "pause_id": "<pause_id>",
    "action": "request_edit",
    "expected_version": 0,
    "admin_user_id": "admin-dev",
    "state_edits": {"price": 1100000},
    "reason_or_comment": "Price corrected per latest stock update"
  }'
```

Then approve:
```bash
curl -X POST http://localhost:8000/hitl/review \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: dev-admin-key" \
  -H "X-Idempotency-Key: $(uuidgen)" \
  -d '{
    "session_id": "test-001",
    "pause_id": "<pause_id>",
    "action": "approve",
    "expected_version": 1,
    "admin_user_id": "admin-dev"
  }'
```

---

## 5. Test Rejection Flow

```bash
curl -X POST http://localhost:8000/hitl/review \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: dev-admin-key" \
  -H "X-Idempotency-Key: $(uuidgen)" \
  -d '{
    "session_id": "test-001",
    "pause_id": "<pause_id>",
    "action": "reject",
    "expected_version": 0,
    "admin_user_id": "admin-dev",
    "reason_or_comment": "Product out of stock — discontinued line"
  }'
```

Expected: Customer receives empathetic message via `customer_support_node`. SupportQueue entry created with `reason="rejected_order"`.

---

## 6. Verify Idempotency (Double-Click Protection)

Send the same X-Idempotency-Key twice:
```bash
IDEM_KEY=$(uuidgen)

# First call
curl -X POST http://localhost:8000/hitl/review \
  -H "X-Idempotency-Key: $IDEM_KEY" \
  # ... payload ...

# Second call (same key)
curl -X POST http://localhost:8000/hitl/review \
  -H "X-Idempotency-Key: $IDEM_KEY" \
  # ... payload ...
```

Expected: Second call returns `200 OK` with `X-Idempotency-Status: hit` — no duplicate processing.

---

## 7. Graph Visualization

```bash
uv run python -m core.agent.graph --export-mermaid
```

Should show new nodes: `hitl_guard_node`, `post_approval_node`, `customer_support_node`.

---

## Key Environment Variables

```bash
# .env (already exists from Week 3)
ADMIN_API_KEY=dev-admin-key           # X-Admin-Key header value
HITL_COST_THRESHOLD_TOKENS=8000      # Cost guard threshold (FR-006)
HITL_CONFIDENCE_THRESHOLD=0.7        # Confidence guard threshold (FR-005)
HITL_TIMEOUT_NOTIFY_MINUTES=30       # 30-min customer notification (FR-016)
HITL_TIMEOUT_ESCALATE_MINUTES=60     # 60-min support escalation (FR-016)
SUPPORT_CONTACT_LINK=https://t.me/support_bot  # Used in rejection messages (FR-018)
```
