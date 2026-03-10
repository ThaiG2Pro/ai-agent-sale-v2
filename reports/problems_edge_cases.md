# Edge Case Scenario Testing Report

**Date:** 2025-07  
**Server model:** qwen3-1.7b (dev, local)  
**10 scenarios run individually, DB + logs inspected per scenario**

---

## Results Summary

| SC   | Scenario                        | Status    | Severity |
|------|---------------------------------|-----------|----------|
| SC01 | Vague browse "bạn có gì không?" | ❌ PROBLEM | MEDIUM   |
| SC02 | Context-less pricing            | ✅ PASS    | —        |
| SC03 | Cancel while HITL paused        | ✅ PASS    | —        |
| SC04 | Mixed intent info+order         | ❌ PROBLEM | MEDIUM   |
| SC05 | Negotiation discount            | ✅ PASS    | —        |
| SC06 | Complaint broken product        | ✅ PASS    | —        |
| SC07 | Out-of-scope coding question    | ❌ PROBLEM | CRITICAL |
| SC08 | MODIFY while paused (new SKU)   | ❌ PROBLEM | HIGH     |
| SC09 | Pronoun reference across turns  | ⚠️ PARTIAL | MEDIUM   |
| SC10 | COMPARISON query retrieval      | ❌ PROBLEM | HIGH     |

---

## Detailed Findings

### SC01 — Vague Browse Query
**Message:** "bạn có gì không?"  
**Expected:** Show product catalog / top categories  
**Actual:** Classified as SMALLTALK → answer_node without retrieval → no products shown  
**Root cause:** Router prompt had no Vietnamese browsing examples; "bạn có gì không?" matches chitchat pattern  
**Fix:** Updated router system prompt to classify browse queries as INFO_QUERY

---

### SC02 — Context-less Pricing ✅
**Message:** "cái đó bao nhiêu?" (with no prior context)  
**Actual:** Classified PRICING → retrieval → showed top-K products  
**Assessment:** Acceptable cold-start behavior

---

### SC03 — Cancel while HITL paused ✅
**Message:** "thôi không mua nữa" (while order paused)  
**Actual:** Keyword heuristic → has_cancel=True → cancellation_node → no order created  
**DB:** QueuedMessage processed, HITL record status=cancelled

---

### SC04 — Mixed Intent (Info + Order in same message)
**Message:** "cho tôi biết laptop nào tốt nhất cho đồ họa dưới 35 triệu, đặt luôn đi"  
**Expected:** Answer info query first (recommend laptop), then confirm order  
**Actual:** ORDER_PLACEMENT wins → skips recommendation → HITL triggered with incomplete context  
**Root cause:** Router selects single primary intent; ORDER_PLACEMENT takes precedence  
**Fix (deferred):** Multi-step routing — answer INFO first, then confirm ORDER on next turn

---

### SC05 — Negotiation Discount ✅
**Message:** "giảm giá thêm 10% được không?"  
**Actual:** Classified NEGOTIATION → escalated to qwen3-4b → polite decline  
**DB:** escalation_flag=True, model_used=premium-chat

---

### SC06 — Complaint Broken Product ✅
**Message:** "sản phẩm tôi mua bị lỗi, tôi muốn khiếu nại"  
**Actual:** COMPLAINT → escalation_node → empathetic response  
**DB:** escalation_flag=True

---

### SC07 — Out-of-Scope Coding Question ❌ CRITICAL
**Message:** "viết cho tôi một đoạn code Python để đọc file CSV"  
**Expected:** Polite decline + redirect to product sales  
**Actual:** Agent wrote full Python code  
**Root cause:** SMALLTALK intent routes to answer_node with no domain guardrail; LLM answered freely  
**Fix:** SMALLTALK path in answer_node uses constrained system prompt that blocks non-sales topics

---

### SC08 — MODIFY While HITL Paused (New Product SKU)
**Message while paused:** "lấy iPhone 14 thay" (change order to iPhone 14)  
**Expected:** Re-pause with new order_info showing iPhone 14  
**Actual:** Re-pause succeeded (hitl_guard_node triggered again) ✅  
  BUT order_info.sku still shows iPhone 15 Pro Max (old product) ❌  
**Root cause:** queue_consumer_node MODIFY path resets HITL state but does not re-resolve product  
**Fix:** Extract new product from queued MODIFY message via retrieval, update order_info

---

### SC09 — Pronoun Reference Across Turns (Partial)
**T1:** "Dell XPS 15 có thông số như thế nào?" → ✅ Correct product info returned  
**T2:** "Nó có phù hợp để làm video editing 4K không?" → ❌ Retrieved LG Monitor instead of Dell XPS  
**T3:** "Ok, tôi quyết định mua cái đó rồi" → ✅ Paused for HITL with correct SKU LAPTOP-DELL-001  
**Root cause:** T2 query "Nó" embeds as neutral pronoun; retrieval fetches top-K by raw embedding without context expansion  
**Fix:** In retrieval_node, expand short/pronoun queries using previous turn's citations

---

### SC10 — COMPARISON Query Retrieval Failure
**T1:** "So sánh MacBook Pro M3 và Dell XPS 15 giúp tôi"  
**Expected:** Side-by-side comparison of both products  
**Actual:** "Tôi không tìm thấy thông tin liên quan" (Layer 1 guard fired)  
**T2:** "Cái nào phù hợp hơn cho lập trình?" → Also "not found" (no T1 context)  
**Root cause:** Comparison query embedding splits attention across two products → cosine similarity < 0.45 threshold; Layer 1 guard declines before answer generation  
**Fix:** For COMPARISON intent: split query by "và/vs/với", run separate searches, merge results

---

## Fixes Implemented

| Fix | File | Status |
|-----|------|--------|
| SC07: SMALLTALK domain guardrail | `core/agent/nodes/answer.py` | ✅ |
| SC01: Router Vietnamese browsing examples | `core/agent/nodes/router.py` | ✅ |
| SC09: Pronoun query expansion | `core/agent/nodes/retrieval.py` | ✅ |
| SC10: COMPARISON query splitting | `core/agent/nodes/retrieval.py` | ✅ |
| SC08: MODIFY product re-resolution | `core/agent/nodes/queue_consumer.py` | ✅ |
| SC04: Mixed intent ordering | Deferred (complex) | ⏳ |
