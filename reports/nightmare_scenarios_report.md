# Nightmare Scenarios Test Report

**Commit:** 6fc53c1  
**Date:** 2026-03-11  
**178 tests pass**

---

## SC1 — Mind-Changer (iPhone → Samsung → question → CANCEL)

### Test Commands
```bash
# Step 1: Order iPhone 15
curl -X POST http://127.0.0.1:8000/agent/query -d '{"session_id":"sc1-1305","message":"Tôi muốn đặt mua iPhone 15"}'
# → HITL paused, pause_id: 019cdb80-62f4-7e70-a3da-8bfd0b6feccd
# → order_info: {sku:PHONE-IP-001, name:iPhone 15 Pro Max 512GB, qty:1}

# Step 2: Queue 3 messages while waiting
curl -X POST .../agent/query -d '{"session_id":"sc1-1305","message":"Thôi lấy Samsung S24 đi."}'
curl -X POST .../agent/query -d '{"session_id":"sc1-1305","message":"À mà con Samsung đó có màu xanh không? Nếu không thì thôi lấy lại iPhone."}'
curl -X POST .../agent/query -d '{"session_id":"sc1-1305","message":"Thôi, tóm lại là Hủy hết đi, không mua bán gì nữa"}'

# Step 3: Admin approves iPhone
curl -X POST .../hitl/review -d '{"session_id":"sc1-1305","pause_id":"...","action":"approve",...}'
```

### Result
```json
{
  "status": "resumed",
  "queue_response": "Your order has been cancelled as requested. If you need further assistance..."
}
```

### DB State
- `queued_messages`: all 5 messages `processed=true` ✅
- `orders`: no order created ✅

### Verdict: **PASS** ✅
CANCEL wins as highest-priority intent. Samsung/question messages processed but overridden.

---

## SC2 — Flash Sale (Xiaomi last unit, stock=0 at approval time)

### Test Commands
```bash
# Set stock to 1
UPDATE agent_v1.products SET stock_quantity=1 WHERE sku='PHONE-XM-001';

# Order
curl -X POST .../agent/query -d '{"session_id":"sc2-1311","message":"Tôi muốn đặt mua Xiaomi 14 Ultra"}'
# → HITL paused, order_info: {sku:PHONE-XM-001, qty:1}

# Simulate another customer buys the last unit
UPDATE agent_v1.products SET stock_quantity=0 WHERE sku='PHONE-XM-001';

# Admin approves (unaware stock is now 0)
curl -X POST .../hitl/review -d '{"action":"approve",...}'
```

### Result
```json
{
  "status": "resumed",
  "queue_response": "I'm sorry... we'd be happy to assist with alternatives or contact support..."
}
```

### DB State
- `orders` WHERE session_id='sc2-1311': **0 rows** ✅
- Re-validation in `state_freshness_validator` caught `stock_quantity=0` → `customer_support_node`

### Verdict: **PASS** ✅
Stock re-validated at approval time. Sold-out product blocked from creating order.

---

## SC3 — Price vs Quantity Conflict

**Scenario:** Admin applies 18M discount, customer queued "Lấy cho tôi 2 cái nhé"

### Test Commands
```bash
# Step 1: Order Lenovo ThinkPad (price: 28,990,000)
curl -X POST .../agent/query -d '{"session_id":"sc3-1400","message":"Tôi muốn đặt 1 cái Laptop Lenovo ThinkPad"}'
# → HITL paused, order_info: {sku:LAPTOP-LENOVO-001, qty:1, price:28990000}

# Step 2: Customer queues qty change
curl -X POST .../agent/query -d '{"session_id":"sc3-1400","message":"Lấy cho tôi 2 cái nhé"}'
# → Queued (processed=false)

# Step 3: Admin approves with discount
curl -X POST .../hitl/review -d '{"action":"approve","approved_price":18000000,...}'
```

### Result
```json
{
  "status": "resumed",
  "new_hitl": {
    "pause_id": "019cdb87-4966-...",
    "reason": "order_approval",
    "state_snapshot": {
      "order_info": {
        "sku": "LAPTOP-LENOVO-001",
        "name": "Lenovo ThinkPad X1 Carbon Gen 12 Intel Core i7",
        "price": 28990000.0,
        "approved_price": 18000000.0,
        "quantity": 2
      }
    }
  }
}
```

### Key Variables
- `sku`: LAPTOP-LENOVO-001 ✅ (no Apple Watch false positive)
- `quantity`: 2 ✅ (customer's change applied)
- `approved_price`: 18,000,000 ✅ (admin's discount preserved)
- `new_hitl`: triggered ✅ (admin must re-confirm the updated order)

### Verdict: **PASS** ✅
System merges admin price + customer qty, re-HITLs for final confirmation.

### Fixes Applied
- `_QTY_CHANGE_PATTERNS` now matches "lấy cho tôi N cái" (gap words between lấy and N)
- `has_product_change` flag separates pure-qty from product-name changes
- Pure qty-change skips RAG entirely → no wrong product returned
- `approved_price` is a dedicated field in `ReviewActionCreate`, merged into `order_info`

---

## SC4 — Angry Customer in Queue (Proactive Escalation)

### Test Commands
```bash
# Step 1: Order MacBook
curl -X POST .../agent/query -d '{"session_id":"sc4-1402","message":"Tôi muốn đặt mua MacBook Pro 16"}'
# → HITL paused

# Step 2: 15 mins later, angry message
curl -X POST .../agent/query -d '{"session_id":"sc4-1402","message":"Làm ăn kiểu gì chậm chạp thế? Thái độ quá tệ! Hủy đơn đi, tôi đi mua chỗ khác!"}'
# → Queued

# Step 3: Admin approves (didn't see the angry msg)
curl -X POST .../hitl/review -d '{"action":"approve",...}'
```

### Result
```json
{
  "status": "resumed",
  "new_hitl": null,
  "queue_response": "Your order has been cancelled as requested..."
}
```

### DB State
- `orders` WHERE session_id='sc4-1402': **0 rows** ✅ 
- `queued_messages`: `processed=true` ✅

### Verdict: **PARTIAL PASS** ⚠️
**What works:** "Hủy đơn" keyword detected → CANCEL wins → order not created even after admin approval.

**Known gap:** No *proactive* admin notification. Admin doesn't know there's a CANCEL in queue before pressing approve. Requires a background task scanning queue for COMPLAINT/CANCEL and alerting admin — out of scope for current sprint.

---

## SC5 — INFO Questions in Queue (Context Mixing)

**Scenario:** MacBook ordered, customer asks charging watts + free bag while waiting

### Test Commands
```bash
# Step 1: Order MacBook
curl -X POST .../agent/query -d '{"session_id":"sc5-1405","message":"Tôi muốn đặt mua MacBook Pro 16"}'
# → HITL paused, product: MacBook Pro 16 inch M3 Pro 18GB

# Step 2: Queue INFO questions
curl -X POST .../agent/query -d '{"session_id":"sc5-1405","message":"Mà con này dùng sạc bao nhiêu Watt nhỉ? Có được tặng túi chống sốc không?"}'
# → Queued (classified as INFO_QUERY, NOT MODIFY_ORDER)

# Step 3: Admin approves
curl -X POST .../hitl/review -d '{"action":"approve",...}'
```

### Result
```json
{
  "status": "resumed",
  "queue_response": "Great news! Your order for MacBook Pro 16 inch M3 Pro 18GB (Quantity: 1) has been successfully placed. Order Reference: sc5-1405\n\nNgoài ra, trả lời câu hỏi của bạn: Mà con dùng sạc bao nhiêu Watt? M3 Pro 18GB dùng sạc 120W. Có thể tặng túi chống sốc."
}
```

### DB State
- `orders` WHERE session_id='sc5-1405': **1 row** (MacBook, status=pending) ✅
- `queued_messages`: `processed=true` ✅
- **No Apple Watch HITL created** ✅

### Key Variables
- Message correctly classified as `OTHER` (not MODIFY_ORDER) via `_INFO_QUERY_PATTERNS`
- `pending_info_questions` stored in AgentState
- `order_execution_node` answered via LLM and appended to confirmation

### Verdict: **PASS** ✅
INFO questions answered inline with order confirmation. No wrong product HITL.

---

## Summary Table

| Scenario | Status | Root Cause Fixed |
|----------|--------|-----------------|
| SC1: Mind-Changer (CANCEL batch) | ✅ PASS | CANCEL priority in `_keyword_classify_batch` |
| SC2: Flash Sale (stock=0 at approval) | ✅ PASS | `state_freshness_validator` re-checks stock |
| SC3: Price vs Qty Conflict | ✅ PASS | `approved_price` field + `has_product_change` tracking |
| SC4: Angry Customer (proactive escalation) | ⚠️ PARTIAL | CANCEL wins; proactive notification = known gap |
| SC5: INFO Questions in queue | ✅ PASS | `_INFO_QUERY_PATTERNS` + `pending_info_questions` |

### Known Limitation (SC4)
Proactive admin alert when COMPLAINT/CANCEL arrives in queue requires:
- A background periodic task (e.g., `asyncio.create_task` with sleep loop)
- Notification mechanism (Telegram admin alert, webhook, etc.)
This is a sprint 2 item.
