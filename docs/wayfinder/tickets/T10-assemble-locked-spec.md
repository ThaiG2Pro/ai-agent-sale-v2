---
id: T10
title: Assemble & Lock Upgrade Spec (openspec change)
type: wayfinder:grilling
mode: HITL
status: closed
assignee: thaivro-main-session
blocked-by: [T05, T06, T07, T08, T09, T11, T12, T13]
---

## Question

Tổng hợp mọi quyết định đã chốt (T05–T09) thành một openspec change /
upgrade-plan duy nhất: danh sách thêm/bớt/điều chỉnh có thứ tự ưu tiên, ràng buộc
zero-cost ghi rõ per-item, eval gate lấy từ Hard-Scenario Inventory (T01), và ranh
giới agent/human theo Delegation Contract (T07). Rà soát mâu thuẫn giữa các quyết
định trước khi khóa. Đây là ticket cuối — đóng nó là map đạt destination.

Chốt luôn tại đây (đủ dữ kiện, không cần ticket riêng):
- **Cắt giảm đường 80%**: quyết định cuối theo verdicts T11.
- **Semantic cache tuning**: dạng an toàn in-graph + invalidation hook price/stock
  (T11) đối chiếu tần suất case từ Hard-Scenario Inventory (T01).

## Resolution

Chốt 2026-08-12. Spec khóa tại
**`openspec/changes/v3-0-agent-effectiveness-resilience/proposal.md`** — tổng hợp
12 quyết định T01–T13 thành 4 nhóm ưu tiên, rà mâu thuẫn (không phát hiện xung đột;
4 điểm giáp ranh khớp chủ đích, ghi trong spec), ràng buộc zero-cost per-item,
eval gate từ T01, ranh giới agent/human theo T06+T07.

Ba quyết định chốt-tại-đây (grilling với owner):
1. **Cắt giảm đường 80% — nhận CẢ 3 ứng viên T11**: SMALLTALK fast-path IN-GRAPH
   (điều kiện: trong graph, gate conservative); keyword pre-classify whitelist
   INFO_QUERY/PRICING/AVAILABILITY + hook invalidation cache khi price/stock update
   (dạng pre-router do-not-cut đã loại); skip memory node CÓ ĐIỀU KIỆN (chỉ khi
   cache-hit/similarity ≥ threshold, không bao giờ FOLLOW_UP/time-reference) — owner
   chọn cắt dù giá trị thấp, khóa kèm đủ điều kiện an toàn T11. Semantic cache tuning
   = chính mục 4.3 (in-graph + invalidation hook).
2. **Thứ tự ưu tiên: nền chống-gãy trước, hiệu quả sau** — P1 intent-flip + FAIL fixes
   → P2 trục order/HITL (draft model, Tier 1, handoff package, Telegram UX) →
   P3 resilience + observability → P4 tool-loop + cắt giảm.
3. **Đóng gói: 1 openspec change `v3-0`** (quy mô vượt dòng v2-x) — SDLC pipeline có
   thể bổ từng nhóm P thành change con khi build.

Đây là ticket cuối — **map đạt destination**.
