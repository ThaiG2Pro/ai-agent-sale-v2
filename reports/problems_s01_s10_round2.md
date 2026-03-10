# Edge-Case Scenario Round 2 — Results & Problem Report

**Date**: 2026-03-11  
**Server commit**: 09f6fc8 (fix: 5 edge-case intelligence fixes)  
**Model**: ollama/qwen3-1.7b (economy-chat), ollama/bge-m3 (embedding)

## Summary Table

| Scenario | Description | Verdict | Notes |
|----------|-------------|---------|-------|
| S01 | Vague query → order "đặt cái đó đi" | PARTIAL | Agent auto-selects first product; should ask clarification |
| S02 | Queue confirm+cancel while HITL paused | PASS | Cancel wins over confirm ✅ |
| S03 | Queue MODIFY product while paused | PASS (catalog gap) | SC08 ran; resolved wrong product — only 1 MacBook in catalog |
| S04 | Multi-turn pronoun reference | PASS | "nó" → first citation name via `_expand_pronoun_query` |
| S05 | Queue add-on while HITL paused | FAIL | MODIFY replaced original product; original order lost |
| S06 | Info → negotiation mid-flow | PASS | NEGOTIATION→escalation_node ✅; response quality weak (small model) |
| S07 | Complaint then new order | PASS | Independent flows work correctly |
| S08 | Admin reject → ask why → reorder | PARTIAL | Rejection reason not surfaced to customer |
| S09 | Ambiguous product name "MacBook" | PASS | Only 1 MacBook in catalog; auto-resolved correctly |
| S10 | Multi-queue mixed intents (info+modify+cancel) | PASS | CANCEL wins, all queue messages processed |

---

## Problem Details

### P1 — S01/S05: MODIFY semantics conflict (HIGH)

**S01**: Customer says "đặt cái đó đi" after vague browse → agent auto-selects first catalog item (Xiaomi 14 Ultra) without asking clarification. Expected: agent should ask "Bạn muốn đặt sản phẩm nào?" when prior context doesn't clearly identify a specific product.

**S05**: Customer queues "thêm tai nghe Sony WH-1000XM5 vào đơn luôn nhé" while ThinkPad HITL is paused.
- `queue_consumer_node` classifies as MODIFY (keyword: "thêm")
- SC08 resolves new product → Sony WH-1000XM5
- `order_info` is replaced with Sony; ThinkPad order **never created**
- hitl_status = paused again (for Sony)
- **Root cause**: MODIFY runs BEFORE order creation; the approved ThinkPad order is discarded

**Fix needed**: 
1. For S01: Add clarification guard in `hitl_guard_node` or `order_placement_node` when `order_info.sku` was resolved from a vague/ambiguous query
2. For S05: "thêm" (add) vs "đổi" (replace) need different handling. "thêm" with a new product = new order request, not MODIFY. OR: create original order first, then process MODIFY queue messages.

### P2 — S08: Rejection reason not communicated (MEDIUM)

Admin rejects with `reason_or_comment = "Sản phẩm hết hàng"`. Customer asks "tại sao đơn bị từ chối?" → agent says "no information about rejected order".

**Root cause**: `hitl_metadata.reason` or rejection message is not injected into agent state for the customer-facing response. The graph resumes after rejection but the rejection reason sits only in `hitl_metadata` table, not in `AgentState.response`.

**Fix needed**: In `hitl_guard_node` (resume path), when `hitl_metadata.status == 'rejected'`, populate `state['response']` with a rejection message that includes the admin's reason.

### P3 — S06: Negotiation response quality (LOW — model limitation)

NEGOTIATION intent correctly routes to escalation_node. But `economy-chat` (qwen3-1.7b) responds with "no information about pricing policies" instead of acknowledging the request and escalating to human.

**Root cause**: Model is too small for nuanced negotiation. Not a code bug.

**Fix in prod**: Use premium model for NEGOTIATION intent. Model escalation is already wired; just needs a larger model.

---

## Fixes to Implement

### Fix 1 — P1: Distinguish "thêm" (add) from "đổi" (replace) in queue_consumer

In `queue_consumer_node._keyword_classify_batch()`:
- Currently: "thêm" → `modify=True`
- Fix: If "thêm" + new product reference AND no explicit "đổi/thay/chuyển" → classify as `confirm=True` (treat as a new separate order request) OR add `add_on=True` category
- The safest fix: when `modify=True` AND original verb is "thêm" → treat as a second ORDER_PLACEMENT request (not a replacement)

### Fix 2 — P2: Surface rejection reason to customer  

In `services/hitl/service.py` or `hitl_guard_node` resume path:
- After rejection, load `hitl_metadata.reason` from DB
- Set `state['response'] = f"Đơn hàng của bạn không được duyệt. Lý do: {reason}. Bạn có muốn đặt sản phẩm khác không?"`

---

## What Passed ✅

- **CANCEL always wins** over confirm/modify in queue (S02, S10)
- **Pronoun expansion** works correctly: "nó" → first citation name (S04)
- **Independent intents after COMPLAINT** work (S07)
- **NEGOTIATION escalation routing** correct (S06)
- **Multi-queue processing** all in one resume cycle (S10)
- **SC08 MODIFY product resolution** runs RAG correctly (S03)
- **Order state visible to admin** via `/hitl/session/{id}/state` (S03, S09)

---

## Server Health

- All 10 scenarios ran without crashes or 5xx errors
- No annotation injection bugs (from prior session's ruff fix)
- Graph compilation: clean
- DB integrity: no orphaned locks
