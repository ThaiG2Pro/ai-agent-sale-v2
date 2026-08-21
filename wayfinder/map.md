---
label: wayfinder:map
title: Agent Effectiveness & Resilience Upgrade Spec
created: 2026-08-11
---

# Map: Agent Effectiveness & Resilience Upgrade Spec

## Destination

Một **spec cải tiến đã khóa** (openspec change / upgrade-plan) chốt trọn bộ quyết định
thêm/bớt/điều chỉnh cho sales agent sao cho: agent tự xử lý tốt ~80% case phổ thông,
nhận diện và bàn giao mượt ~20% case khó cho human có chuyên môn, xử lý được các
scenario hội thoại + order khó (kể cả ý định khách đảo chiều 180° giữa hai câu liền
nhau), và bền với resource có hạn ở lượng user vừa phải — **tất cả trong zero-cost
stack** (Groq free tier / model local + fastembed, theo ADR-001/ADR-006). Spec sẵn
sàng đưa vào SDLC pipeline để build; map này KHÔNG implement.

## Notes

- **Ràng buộc cứng**: zero-cost stack — model mạnh là nice-to-have, mọi thiết kế phải
  chạy được với model nhỏ (qwen3-4b tier / Groq free); không thêm dịch vụ trả phí.
- **Gộp v2-6**: draft-order redesign (agent chỉ tạo draft, không sửa đơn cũ; 5 open
  questions từ session 2026-08-10) được gộp vào map này — các câu hỏi của nó là ticket
  ở đây.
- Dự án hiện scoped là **CV demo** (quyết định 2026-08-05) — spec hướng "production
  parity" để trình bày, không phải vận hành thật.
- Skills nên dùng khi làm ticket: `edge-case-enumerator`, `assumption-detector`,
  `clarification-generator` (thay cho /grilling · /domain-modeling — không có trong repo).
- **Tracker conventions (local-markdown)**: ticket = file trong `wayfinder/tickets/`,
  frontmatter `status: open|closed`, `assignee` (rỗng = unclaimed; điền TRƯỚC khi làm),
  `blocked-by: [ids]`. Frontier = open + unblocked + unassigned. Resolution ghi vào
  section `## Resolution` cuối file ticket rồi đóng (`status: closed`) và thêm 1 dòng
  vào Decisions-so-far ở đây. Research findings để trong `wayfinder/research/`.

## Decisions so far

<!-- one line per closed ticket: [title](tickets/file.md) — gist -->

- [Research: Multi-turn Intent Tracking cho model nhỏ](tickets/T03-multiturn-intent-tracking.md) — khuyến nghị history-aware router (3 turn cuối + previous_intent) + sticky intent transition table + ngừng wipe intent trong `make_initial_state`, 0 LLM call thêm/turn; defer query rewriting. Chi tiết: `research/multiturn-intent-tracking.md`.
- [Research: Small-Model Tool-Calling Reliability](tickets/T02-small-model-tool-calling.md) — verdict **feasible-with-guardrails**: tool-calling chỉ BỔ SUNG router cho mặt tư vấn read-only, driver là Groq llama-3.3-70b (qwen3-4b local chỉ fallback single-shot — multi-turn BFCL sụp còn ~35%); order/HITL giữ state machine; 8 guardrails trong `research/small-model-tool-calling.md`.
- [Research: Groq limits + LiteLLM fallback patterns](tickets/T04-rate-limits-fallback.md) — Groq free tier: 70b bị chặn bởi **100K TPD** (~25-50 turn RAG nặng/ngày), 8b thoáng hơn (500K TPD); router hiện KHÔNG có retry/timeout/fallback; menu degrade: fallback chain → cache-only → retrieval-only → holding+queue (bảng có sẵn dùng được); backpressure bằng Postgres SKIP LOCKED, không cần Redis. Chi tiết: `research/rate-limits-fallback.md`.
- [Router & Intent-Gap Architecture](tickets/T08-router-intent-gap-architecture.md) — **Hybrid escalation**: router enum giữ đường 80% (model nhỏ), tool-loop 70b chỉ cho ~20% turn mơ hồ với guardrails G1–G8; intent-flip fix bằng full combo T03 (0 call thêm); confidence giữ threshold + tinh chỉnh; câu "cắt gì trên đường 80%" tách ra T11.
- [Research: Impact Analysis — 3 ứng viên cắt giảm](tickets/T11-cost-cut-impact-analysis.md) — SMALLTALK fast-path: **safe-with-conditions** (phải nằm trong graph để không bỏ đói combo T03); cache trước router: **do-not-cut** ở dạng đề xuất (bypass HITL-pause + routing, chỉ tiết kiệm 1 call — dạng an toàn là in-graph); skip memory node: safe nhưng **giá trị thấp** (node 0 LLM call, đang rescue borderline). Chi tiết: `research/cost-cut-impact-analysis.md`; chốt cắt tại T10.
- [Hard-Scenario Inventory & Candidate Eval Set](tickets/T01-hard-scenario-inventory.md) — kiểm kê **55 hard case** (order-edge 28 · hội-thoại-khó 13 · intent-flip 5 · hạ-tầng 9): ~27 pass, **8 FAIL đang mở** (ADD-ON mất đơn gốc, flip "đổi ý" khi paused bị gán FOLLOW_UP, reject reason không đến khách…), 17 untested; gap trắng: intent-flip ngoài HITL zero test, gold_dataset chỉ phủ RAG, không có test 429. Đề cử 14 case vào eval set. Tài liệu v2-6 gốc thất lạc — 5 open questions không khôi phục được. Chi tiết: `research/hard-scenario-inventory.md`.
- [Draft-Order Data Model & Lifecycle](tickets/T05-draft-order-data-model.md) — draft = row `orders` với status lifecycle (draft/pending_review/confirmed/cancelled/superseded/expired) + cột `supersedes_id`, KHÔNG bảng mới (tiền đề v2-6 lỗi thời: cột status đã tồn tại); `order_info.items[]` multi-item (giải gốc O14 ADD-ON + gap O12 combo); soft cap 3 active/khách, TTL 24h; supersede inline cùng transaction + expiry lazy lúc đọc, không xóa row, không background job.
- [Hard-Conversation Policy](tickets/T06-hard-conversation-policy.md) — bảng policy per-intent: trả giá → draft giá gốc + note "xin giảm" cho human (agent được nêu KM đang chạy, không counter); khiếu nại → xoa dịu + thu 3 fact (≤2 lượt) rồi luôn chuyển; "nhận thua" tách 2 đường: mơ hồ-trong-catalog → handoff, ngoài-catalog → decline + gợi ý gần nhất; đa ý định: CANCEL > COMPLAINT > NEGOTIATION > ORDER > INFO, xử nhánh cao nhất + acknowledge phần còn lại qua `secondary_intents` (fix H6).
- [80/20 Delegation Contract — risk tiers + human handoff package](tickets/T07-delegation-contract.md) — Tier 1 khôi phục **auto-proceed có điều kiện** (default <10M VND, SP xác định duy nhất, đủ SĐT/địa chỉ, stock đủ; safety invariant giữ); tiêu chí 20% = risk score composite ∨ NEGOTIATION/COMPLAINT ∨ clarify-loop ≥2 ∨ degraded (429/offline); metric = **deflection rate theo session ≥ 80%**; handoff package 4 phần bắt buộc (tóm tắt + lý do structured, draft order, intent log, gợi ý hành động); đường về giữ queue_consumer + 2 nghĩa vụ: reject reason đến khách (O27), eval gate cho nhánh LLM fallback (F2).
- [Resilience & Degradation Policy](tickets/T09-resilience-degradation-policy.md) — SLO thiết kế: ~10 user đồng thời / ~30 msg/phút (demo scale); fallback ladder A+B+D rẽ theo intent: Groq 70b → 8b → Ollama → cache-only → holding+queue, nhưng ORDER/NEGOTIATION/COMPLAINT không ăn rung local/cache — đi thẳng queue→human (khớp T07); tắt premium tool-loop trước, escalation về economy sau; cap tổng ~30s/turn, 429 không retry mà fallback ngay; backpressure = semaphore + `queued_messages` SKIP LOCKED + token-budget gate app-side đếm TPD trong Postgres.
- [Observability mức demo — đo deflection rate & alert handoff](tickets/T12-observability-demo.md) — endpoint stats JSON dưới `/admin` (deflection rate từ `support_queue`+`interrupted_sessions` theo T07, queue depth, đếm degraded turn) + mini dashboard tĩnh trên khung ui.py/static sẵn có; alert bộ đủ 3 loại qua Telegram admin trên loop `timeout_scheduler`: queue>N, case chờ>X phút (đóng gap O28), degrade event (nối T09) — N=5/X=10' default config; log 80/20 = span attributes trên trace Phoenix sẵn có (intent, model_used, risk_signals, outcome), zero bảng mới.
- [Telegram handoff UX — hiển thị package bàn giao & human-reply qua webhook](tickets/T13-telegram-handoff-ux.md) — tin admin 1 message HTML: 3 phần thẳng (tóm tắt+lý do, draft snapshot, gợi ý) + intent log thu sau nút callback; thao tác = inline buttons [Duyệt]/[Counter]/[Từ chối] đổ vào flow `/hitl/review` sẵn có, Counter/Từ chối hỏi tiếp reason bằng force-reply (reason bắt buộc → O27 luôn có nội dung); khách nhận holding hẹn "~X phút" (khớp threshold T12) và kết quả luôn kèm reason: approve xác nhận, counter nêu giá mới, reject lý do + gợi ý tiếp (đóng FAIL O27).
- [Assemble & Lock Upgrade Spec](tickets/T10-assemble-locked-spec.md) — **SPEC KHÓA tại `openspec/changes/v3-0-agent-effectiveness-resilience/proposal.md`**: 12 quyết định lắp thành 4 nhóm ưu tiên (P1 nền chống-gãy → P2 trục order/HITL → P3 resilience+observability → P4 tool-loop+cắt giảm); chốt cắt cả 3 ứng viên T11 kèm điều kiện an toàn (SMALLTALK in-graph, keyword whitelist + cache invalidation hook, skip memory có điều kiện); rà mâu thuẫn: không xung đột. **Map đạt destination 2026-08-12.**

## Not yet specified

- *(trống — map hoàn tất, 13/13 ticket đóng)*

## Out of scope

- **Thiết kế bắt buộc model mạnh/trả phí** — trái ràng buộc zero-cost (model mạnh chỉ
  được là optional tier).
- **Scale lớn / multi-tenant / deployment thật** — dự án là CV demo, "user vừa phải" là
  trần.
- **Migrate khỏi LangGraph/stack hiện tại** — đổi framework không phục vụ destination.
- **Kênh mới (voice, web widget…)** — chỉ tối ưu các kênh đang có.
