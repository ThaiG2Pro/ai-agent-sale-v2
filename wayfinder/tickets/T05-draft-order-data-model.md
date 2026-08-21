---
id: T05
title: Draft-Order Data Model & Lifecycle (v2-6 Q1–Q3)
type: wayfinder:grilling
mode: HITL
status: closed
assignee: thaivro-main-session
blocked-by: []
---

## Question

Chốt data model + vòng đời draft order (gộp từ v2-6):
1. Bảng `draft_orders` riêng hay thêm cột `status` vào bảng `orders` hiện tại
   (schema hiện KHÔNG có cột status)?
2. Soft cap số draft mỗi khách + TTL cho draft cũ là bao nhiêu?
3. Ai/khi nào dọn draft "superseded" (agent không sửa draft — chỉ tạo mới)?

Ràng buộc từ redesign đã quyết: agent chỉ chào hỏi + tư vấn + tạo draft; user tự
thanh toán; agent không bao giờ sửa draft đã tạo — chỉ tạo draft mới thay thế.

## Resolution

Chốt 2026-08-12 qua grilling với owner. Lưu ý tiền đề v2-6 Q1 đã lỗi thời: bảng
`orders` HIỆN ĐÃ có cột `status` (String(20), default "pending"; `order_execution`
insert "confirmed") — không còn là "schema không có cột status".

1. **Storage**: KHÔNG tạo bảng mới — draft là row trong `orders` với vòng đời
   `status ∈ {draft, pending_review, confirmed, cancelled, superseded, expired}`
   + thêm cột `supersedes_id` (nullable, self-FK) để lần được chuỗi draft thay thế.
2. **Multi-item**: draft model chuyển sang `order_info.items[]` =
   `[{product_id, qty, price}, …]` — giải quyết tận gốc bug O14 (ADD-ON = tạo draft
   mới gồm items cũ + item mới, không mất đơn gốc) và mở đường combo O12;
   `order_execution` đọc items[] khi confirm. Agent vẫn KHÔNG edit draft — chỉ tạo mới.
3. **Cap & TTL**: soft cap **3 draft active/khách** (tạo draft thứ 4 → supersede draft
   active cũ nhất) · TTL **24h** — quá hạn coi như expired, agent báo giá lại thay vì
   confirm draft cũ (state_freshness vẫn re-validate giá/stock lúc confirm).
4. **Cleanup**: supersede **inline** — tạo draft mới thì flip draft bị thay thành
   `superseded` trong cùng transaction; expiry check **lazy lúc đọc** (filter theo
   `created_at`, không sửa row); KHÔNG xóa row, không background job — giữ audit trail
   chuỗi đổi ý cho demo, zero infra mới.
