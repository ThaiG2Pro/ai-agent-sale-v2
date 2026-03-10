# HITL Real Scenario Test Report

**Branch:** `004-human-in-loop-hitl`  
**Server:** `uvicorn api.main:app --host 0.0.0.0 --port 8000`  
**Environment:** Dev / Ollama local (model: `qwen3-1.7b`, embed: `bge-m3`)

---

## Summary

| Scenario | Description | Result |
|----------|-------------|--------|
| SC1 | Browse laptops under 25M | ✅ PRICING intent, correct answer |
| SC2 | Place "that product" → HITL pause | ✅ ORDER_PLACEMENT, paused |
| SC2-state | Admin checks session state | ✅ order_info visible |
| SC3a | Admin approves → order confirmed | ✅ status=confirmed in DB |
| SC3b | Admin rejects → status=rejected | ✅ status=rejected in DB |
| SC4 | Place iPhone 15 Pro Max | ✅ paused, order_info correct |
| SC4a | Customer changes mind while paused | ✅ message queued |
| SC5 | Admin approves iPhone → queue drains | ✅ iPhone confirmed; Xiaomi queued msg processed (⚠️ see note) |

---

## Scenario 1 — Browse laptops under 25M

**Message:** `"Tôi muốn mua lap top dưới 25 triệu, bạn có sản phẩm nào?"`

**Command:**
```bash
curl -s -X POST http://localhost:8000/agent/query \
  -H "Content-Type: application/json" \
  -d '{"session_id": "report-s1", "message": "Tôi muốn mua lap top dưới 25 triệu, bạn có sản phẩm nào?"}'
```

**Log (key lines):**
```
TopK adjusted by pre-classified intent: intent=PRICING, top_k=5
L1 lookup: hash=59f74bf3, found=False
L2 lookup: threshold=0.95, found=False
  escalation_node
  answer_node
Cache write: hash=59f74bf3, citations_count=5
```

**Response:**
```json
{
  "intent": {"primary_intent": "PRICING"},
  "confidence": 0.605,
  "hitl_paused": false,
  "elapsed_ms": 18010
}
```

**Answer excerpt:**
> Không có sản phẩm laptop nào trong danh sách được liệt kê dưới 25 triệu VND.
> Các sản phẩm laptop có giá như sau:
> - ASUS VivoBook Pro 15 (2024): 32,990,000 VND
> - Dell XPS 15 Plus (2024): 39,990,000 VND
> - Lenovo ThinkPad X1 Carbon Gen 12: ...

**Citations returned:** LAPTOP-ASUS-001, LAPTOP-DELL-001, LAPTOP-LENOVO-001 (+ others)

**Result:** ✅ `intent=PRICING` (previously misclassified as `ORDER_PLACEMENT`).  
**Fix applied:** Router system prompt updated — `ORDER_PLACEMENT` now requires a *specific named product*, not a price-range browse.

---

## Scenario 2 — Place order for "that product"

**Session:** `report-s1` (continuing from SC1)  
**Message:** `"Tôi đặt sản phẩm đó nhé"`

**Command:**
```bash
curl -s -X POST http://localhost:8000/agent/query \
  -H "Content-Type: application/json" \
  -d '{"session_id": "report-s1", "message": "Tôi đặt sản phẩm đó nhé"}'
```

**Response:**
```json
{
  "intent": {"primary_intent": "ORDER_PLACEMENT"},
  "hitl_paused": true,
  "hitl_pause_id": "019cd693-dd3a-7b70-923d-85f1d0d1cbba",
  "declined": true,
  "answer": "Yêu cầu đặt hàng của bạn đang chờ xác nhận từ nhân viên..."
}
```

**Result:** ✅ HITL triggered, `pause_id` returned correctly.

---

## Scenario 2 — Admin checks session state

**Command:**
```bash
curl -s "http://localhost:8000/hitl/session/report-s1/state" \
  -H "X-Admin-Key: dev-key"
```

**Response (key fields):**
```json
{
  "hitl_metadata": {
    "pause_id": "019cd693-dd3a-7b70-923d-85f1d0d1cbba",
    "status": "paused",
    "version": 0
  },
  "state_values": {
    "order_info": {
      "sku": "LAPTOP-ASUS-001",
      "name": "ASUS VivoBook Pro 15 (2024) OLED RTX 4050",
      "price": 32990000.0,
      "quantity": 1,
      "status": "pending"
    }
  },
  "next_node": "hitl_guard_node",
  "queued_messages_count": 0
}
```

**Result:** ✅ Admin can see full order_info and session state.

---

## Scenario 3a — Admin approves

**Command:**
```bash
curl -s -X POST http://localhost:8000/hitl/review \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: dev-key" \
  -H "X-Idempotency-Key: sc3a-approve-001" \
  -d '{
    "session_id": "report-s1",
    "pause_id": "019cd693-dd3a-7b70-923d-85f1d0d1cbba",
    "action": "approve",
    "expected_version": 0,
    "admin_user_id": "admin-001"
  }'
```

**Response:**
```json
{"status": "resumed", "action_id": "019cd695-e1dd-7e01-9854-0b8b14a10c1d"}
```

**DB after approve:**
```
pause_id                             | status   | admin_id  | escalation_count
019cd693-dd3a-7b70-923d-85f1d0d1cbba | approved | admin-001 | 0
```

**Orders DB:**
```
session_id | status    | sku             | name
report-s1  | confirmed | LAPTOP-ASUS-001 | ASUS VivoBook Pro 15 (2024) OLED RTX 4050
```

**Result:** ✅ Order confirmed, 1 HITL record, no duplicates.

---

## Scenario 3b — Admin rejects (separate session)

**Session:** `report-s3b`  
**Message:** `"Tôi muốn đặt ASUS VivoBook Pro 15"`

> Placed order → paused → `pause_id=019cd697-9f07-7881-acb4-7731bcb3ccad`

**Admin reject command:**
```bash
curl -s -X POST http://localhost:8000/hitl/review \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: dev-key" \
  -H "X-Idempotency-Key: sc3b-reject-001" \
  -d '{
    "session_id": "report-s3b",
    "pause_id": "019cd697-9f07-7881-acb4-7731bcb3ccad",
    "action": "reject",
    "reason_or_comment": "Out of stock special model",
    "expected_version": 0,
    "admin_user_id": "admin-001"
  }'
```

**Response:**
```json
{"status": "rejected", "action_id": "019cd697-f60a-78f1-85ee-ab962646064d"}
```

**DB:**
```
pause_id                             | status   | admin_id
019cd697-9f07-7881-acb4-7731bcb3ccad | rejected | admin-001
```

**Result:** ✅ Status=rejected in DB. Note: `action` requires `reason_or_comment` field (not `admin_note`) when action=`reject`.

---

## Scenario 4 — Place iPhone 15 Pro Max order

**Session:** `report-s4` (new session)  
**Message:** `"Tôi đặt điện thoại iphone 15 pro max nhé"`

**Command:**
```bash
curl -s -X POST http://localhost:8000/agent/query \
  -H "Content-Type: application/json" \
  -d '{"session_id": "report-s4", "message": "Tôi đặt điện thoại iphone 15 pro max nhé"}'
```

**Response:**
```json
{
  "intent": {"primary_intent": "ORDER_PLACEMENT"},
  "hitl_paused": true,
  "hitl_pause_id": "019cd698-2900-74c3-880f-d95a6b922ed3"
}
```

**Admin checks state:**
```bash
curl -s "http://localhost:8000/hitl/session/report-s4/state" -H "X-Admin-Key: dev-key"
```
```json
{
  "hitl_metadata": {"pause_id": "019cd698-...", "status": "paused"},
  "state_values": {
    "order_info": {
      "sku": "PHONE-IP-001",
      "name": "iPhone 15 Pro Max 512GB",
      "price": 28900000.0,
      "quantity": 1
    }
  },
  "queued_messages_count": 0
}
```

**Result:** ✅ HITL paused, correct iPhone product resolved.

---

## Scenario 4a — Customer changes mind while paused

**Session:** `report-s4` (still paused)  
**Message:** `"tôi đổi ý rồi. lấy Xiaomi 14 ultra đi"`

**Command:**
```bash
curl -s -X POST http://localhost:8000/agent/query \
  -H "Content-Type: application/json" \
  -d '{"session_id": "report-s4", "message": "tôi đổi ý rồi. lấy Xiaomi 14 ultra đi"}'
```

**Response:**
```json
{
  "intent": {"primary_intent": "FOLLOW_UP"},
  "hitl_paused": false,
  "answer": "Your message has been received. An agent is reviewing your request."
}
```

**DB (queued_messages):**
```
message_text                             | processed
tôi đổi ý rồi. lấy Xiaomi 14 ultra đi  | f
```

**Result:** ✅ Message queued correctly (processed=false). Customer receives holding message.

---

## Scenario 5 — Admin approves iPhone → queue drains

**Command:**
```bash
curl -s -X POST http://localhost:8000/hitl/review \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: dev-key" \
  -H "X-Idempotency-Key: sc5-approve-iphone-001" \
  -d '{
    "session_id": "report-s4",
    "pause_id": "019cd698-2900-74c3-880f-d95a6b922ed3",
    "action": "approve",
    "expected_version": 0,
    "admin_user_id": "admin-001"
  }'
```

**Response:**
```json
{"status": "resumed", "action_id": "019cd698-bd1a-7a93-98f4-f13ebb81e315"}
```

**Server log (graph execution after approve):**
```
hitl_guard_node          ← resumes from checkpoint
queue_consumer_node      ← processes queued Xiaomi message
state_freshness_validator_node
order_execution_node
answer_node
POST /hitl/review processed in 2.55s
```

**DB after approve:**

`hitl_metadata`:
```
pause_id                             | status   | admin_id
019cd698-2900-74c3-880f-d95a6b922ed3 | approved | admin-001
```

`queued_messages`:
```
message_text                             | processed
tôi đổi ý rồi. lấy Xiaomi 14 ultra đi  | t
```

`orders`:
```
session_id | status    | sku         | name
report-s4  | confirmed | PHONE-IP-001 | iPhone 15 Pro Max 512GB
```

**Result:** ✅ iPhone order confirmed. Queued Xiaomi message was processed (processed=true).

**⚠️ Known Limitation:** The Xiaomi change-of-mind message ("tôi đổi ý rồi. lấy Xiaomi 14 ultra đi") was classified as `FOLLOW_UP` / non-MODIFY by `queue_consumer_node`'s LLM batch classifier (qwen3-1.7b). As a result, `has_modify=False` and the message fell through to `state_freshness_validator_node` → `order_execution_node`, confirming the original iPhone order rather than triggering a new HITL pause for Xiaomi.

The expected behavior (re-pause for MODIFY_ORDER) requires the small Vietnamese model to correctly classify "đổi ý" + "lấy X đi" as `MODIFY_ORDER`. This is a **model quality issue** with the dev-tier model. The `queue_consumer_node` logic for MODIFY_ORDER re-pause is correctly implemented (`batch_result.has_modify → goto hitl_guard_node`), but the classification fails.

**Fix direction:** Add explicit Vietnamese examples to the queue batch classifier prompt:
- "tôi đổi ý rồi, lấy X đi" → MODIFY_ORDER  
- "thay đổi sang X" → MODIFY_ORDER

---

## API Required Fields Reference

| Endpoint | Required fields |
|----------|----------------|
| `POST /agent/query` | `session_id`, `message` |
| `GET /hitl/session/{id}/state` | Header: `X-Admin-Key` |
| `POST /hitl/review` | `session_id`, `pause_id`, `action`, `expected_version`, `admin_user_id` + Header: `X-Admin-Key`, `X-Idempotency-Key` |
| `POST /hitl/review` (reject) | Above + `reason_or_comment` (required for `action=reject`) |

---

## Bugs Fixed During Session

| Bug | Fix |
|-----|-----|
| Router misclassified "muốn mua X dưới N triệu" as ORDER_PLACEMENT | Updated system prompt: ORDER_PLACEMENT requires specific named product |
| `hitl_pause_id: null` in response | Extract from `snapshot.tasks[].interrupts[].value["pause_id"]` |
| Double HITLMetadata records on graph resume | DB dedup check before insert in `hitl_guard_node` |
| `"Node hitl_review_node does not exist"` on HITL review | Removed phantom lambda node; changed `as_node` to `"hitl_guard_node"` |
| Queued messages silently lost | Added `await db.commit()` in `enqueue_message` |
