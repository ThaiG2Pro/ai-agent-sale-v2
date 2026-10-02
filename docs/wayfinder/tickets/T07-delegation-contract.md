---
id: T07
title: 80/20 Delegation Contract — risk tiers + human handoff package (v2-6 Q5)
type: wayfinder:grilling
mode: HITL
status: closed
assignee: thaivro-main-session
blocked-by: [T01]
---

## Question

Chốt "hợp đồng" phân công agent/human:
1. **Risk-tier drift** (v2-6 Q5): Tier 1 hiện là dead code (mọi order đều interrupt
   như Tier 2). Sửa cho Tier 1 auto-proceed thật, hay chấp nhận mọi order là Tier 2?
   Ngưỡng `HITL_RISK_TIER*` có cần tune lại theo inventory từ T01?
2. **Tiêu chí 20%**: tín hiệu nào đẩy case sang human (risk score, confidence, intent
   loại, khách VIP/mới, giá trị đơn) — và đo "80% agent tự xử lý" bằng metric gì?
3. **Handoff package**: human nhận được gì khi được bàn giao (tóm tắt hội thoại,
   intent log, draft order, lý do escalate) — hiện support_queue chứa gì, thiếu gì?
4. Human trả lời xong thì luồng quay về agent thế nào (queue_consumer hiện làm gì,
   giữ hay đơn giản hóa)?

Blocked by **Hard-Scenario Inventory (T01)** — cần biết 20% thực tế gồm những case
nào trước khi chốt tiêu chí.

## Resolution

Chốt 2026-08-12 qua grilling với owner. Hợp đồng phân công agent/human:

1. **Risk-tier**: khôi phục **Tier 1 auto-proceed CÓ ĐIỀU KIỆN** — agent tự tạo đơn
   không chờ admin khi hội đủ: giá trị < ngưỡng Tier 1 (**default 10 triệu VND**, vẫn
   là config), sản phẩm xác định duy nhất (không mơ hồ — chặn lỗi O5/F4), đủ SĐT +
   địa chỉ, stock đủ, khách không bị flag rủi ro. Safety invariant giữ nguyên: giá trị
   unknown/cao → luôn ≥ Tier 2. Bỏ hardcode `ORDER_PLACEMENT → Tier 2` trong
   `_resolve_risk_tier` (`core/agent/nodes/hitl_guard.py`).
2. **Tiêu chí 20% (đẩy sang human)** — 4 tín hiệu, OR nhau:
   - Risk score composite hiện có (weights conf/value/history; tune ngưỡng theo tần
     suất từ Hard-Scenario Inventory T01);
   - Intent cứng NEGOTIATION / COMPLAINT;
   - Clarify loop ≥ 2 lần không xác định được SP/ý định → chuyển human thay vì hỏi tiếp;
   - Hạ tầng degraded (Groq 429 / model offline) → holding message + queue human
     (khớp menu degrade từ T04).
   **Metric**: **deflection rate theo session** — % session kết thúc không cần human
   (không vào support_queue, không pause Tier ≥ 2), target ≥ 80%; đo được ngay từ
   `support_queue` + `interrupted_sessions`, không cần instrument thêm.
3. **Handoff package chuẩn hóa** — 4 thành phần BẮT BUỘC (thay `reason` 50 ký tự +
   `context_snapshot` JSONB tự do):
   - Tóm tắt hội thoại (tái dùng `conversation_summaries`) + lý do escalate structured
     (tín hiệu nào trong 4 tín hiệu đã bắn);
   - Draft order snapshot (schema cụ thể chốt ở Draft-Order Data Model T05 — ở đây chỉ
     chốt là bắt buộc có mặt);
   - Intent log + trạng thái khách (`intent_tracking` status + `sales_intent_logs` gần nhất);
   - Gợi ý hành động cho human (1-2 action đề xuất kèm lý do — chấp nhận +1 LLM call
     lúc escalate).
4. **Đường về**: GIỮ kiến trúc `queue_consumer` (keyword pre-classify → LLM fallback)
   + 2 nghĩa vụ contract: (a) reason admin reject/approve PHẢI được đưa vào response
   cho khách (fix O27); (b) nhánh LLM fallback phải qua eval gate — case F2 "đổi ý"
   vào eval set để chặn regress.
