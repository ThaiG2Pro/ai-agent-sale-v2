# Quickstart: Async Persistence & Memory

**Week 5** | Branch: `005-async-persistence-memory`  
**Prerequisite**: Week 4 branch merged. `docker compose up` running with Postgres 17 + pgvector.

---

## 1. Run the Migration

```bash
# Add 4 new tables to agent_v1 schema
uv run alembic upgrade head
```

Expected new tables in `agent_v1`:
- `conversation_summaries`
- `semantic_memory` (with HNSW index `idx_semantic_memory_embedding`)
- `sales_intent_log`
- `intent_tracking`

Verify:
```bash
docker exec -it <postgres-container> psql -U agent -d agent_db -c \
  "\dt agent_v1.*" | grep -E "summaries|semantic_memory|intent"
```

---

## 2. Run Contract Tests First (Article IV — TDD)

```bash
# Contract tests must FAIL before implementation (Red phase)
uv run pytest tests/contract/test_memory_api.py -v
```

All should return 404/422 (routes not yet registered). This is correct.

---

## 3. Smoke Test: Post-Turn Background Tasks

Start the dev server:
```bash
uv run uvicorn main:app --reload --log-level debug
```

Send a test message that triggers intent extraction:
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Tôi cần mua 3 máy điều hoà, ngân sách khoảng 30 triệu, cần gấp trong tuần này",
    "session_id": "test-session-001",
    "customer_id": "test-customer-001"
  }'
```

Expected behaviour (check logs):
```
INFO  post_turn_tasks dispatched [session=test-session-001]
INFO  intent_extractor: PRICING detected → extraction running
INFO  intent_tracker: upsert customer_id=test-customer-001 version=1→2
INFO  summarizer: 1 messages < threshold (20), skipping
```

Verify intent was stored:
```bash
curl http://localhost:8000/memory/intent/test-customer-001 \
  -H "X-Admin-Key: dev-admin-key"
```

Expected response:
```json
{
  "customer_id": "test-customer-001",
  "urgency_level": "HIGH",
  "budget_range": "khoảng 30 triệu",
  "product_interest": ["máy điều hoà"],
  "decision_timeline": "trong tuần này",
  "intent_status": "ENGAGED",
  "version": 2
}
```

---

## 4. Smoke Test: Conversation Summarization

Send 20+ messages to trigger summarization:
```bash
# Use the CLI to seed a long conversation
uv run python -m cli.rag_admin seed-long-conversation \
  --session-id test-session-002 \
  --customer-id test-customer-002 \
  --message-count 22
```

Check that a summary was created:
```bash
curl http://localhost:8000/memory/semantic/test-customer-002 \
  -H "X-Admin-Key: dev-admin-key"
```

Expected response includes an ACTIVE semantic memory entry linked to a summary with `products_discussed` populated.

---

## 5. Smoke Test: Restart Resilience (FR-001)

```bash
# Step 1: Start a conversation that triggers a HITL pause
curl -X POST http://localhost:8000/chat \
  -d '{"message": "Tôi muốn đặt 5 cái quạt công nghiệp", "session_id": "test-session-003", "customer_id": "test-customer-003"}'

# Step 2: Kill the server
pkill -f uvicorn

# Step 3: Restart
uv run uvicorn main:app --reload

# Step 4: Check pending HITL is still there
curl http://localhost:8000/hitl/session/test-session-003/state \
  -H "X-Admin-Key: dev-admin-key"
# Expected: 200 with hitl_triggered=true, order_info intact
```

---

## 6. Smoke Test: Semantic Memory Recall

```bash
# Session 1: Store budget context
curl -X POST http://localhost:8000/chat \
  -d '{"message": "Ngân sách tôi là 20 triệu cho máy lạnh", "session_id": "session-A", "customer_id": "customer-X"}'

# [Wait for summarization threshold OR manually trigger]
uv run python -m cli.rag_admin trigger-summary --session-id session-A

# Session 2: New session, same customer
curl -X POST http://localhost:8000/chat \
  -d '{"message": "Tôi muốn mua thêm thiết bị", "session_id": "session-B", "customer_id": "customer-X"}'

# The agent should reference the 20M budget from session-A in its response
```

---

## 7. Test Optimistic Lock (Race Condition)

```bash
uv run pytest tests/unit/test_intent_tracker.py::test_concurrent_updates -v
```

Expected: Both concurrent tasks complete without data loss; `version` field increments correctly; no `IntegrityError`.

---

## 8. Unit Tests

```bash
uv run pytest tests/unit/ -v --tb=short -k "memory or intent or summarizer"
```

---

## 9. Integration Tests

```bash
uv run pytest tests/integration/test_memory_flow.py -v
```

Key scenarios covered:
- `test_restart_preserves_intent` — kill + restart → intent record intact
- `test_summary_triggers_at_threshold` — 20 messages → summary created
- `test_semantic_recall_cross_session` — session A memory recalled in session B
- `test_stale_embedding_excluded` — change EMBED_MODEL env → old entries excluded from search
- `test_rtbf_with_pending_hitl` — delete request → 409 without confirm, 200 with confirm

---

## 10. Config Reference

| Variable | Default | Purpose |
|----------|---------|---------|
| `MEMORY_SUMMARY_THRESHOLD` | `20` | Messages before summarization triggers |
| `MEMORY_RELEVANCE_THRESHOLD` | `0.75` | Min cosine score for memory retrieval |
| `MEMORY_TOP_K` | `3` | Max past summaries returned per query |
| `CHECKPOINT_SIZE_WARN_BYTES` | `1048576` | 1MB checkpoint size warning threshold |
| `CHECKPOINT_RETENTION_DAYS` | `90` | Cleanup eligibility for resolved checkpoints |

All configurable via `.env` or environment variables — no hardcoded values.

---

## 11. Week 6 Integration Notes

When wiring Telegram (Week 6), populate `customer_id` and `session_id` from the webhook:

```python
# api/routes/telegram.py (Week 6)
customer_id = str(update.effective_user.id)          # Telegram user ID → string
session_id = f"telegram:{update.effective_chat.id}"  # Namespaced thread_id

state = make_initial_state(
    user_message=message.text,
    session_id=session_id,
    customer_id=customer_id,   # Week 5 field — already required
)
```

No changes to Week 5 memory services needed. They are already keyed by `customer_id` and `session_id`.
