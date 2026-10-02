---
id: T09
title: Resilience & Degradation Policy — target load, fallback chain, backpressure
type: wayfinder:grilling
mode: HITL
status: closed
assignee: thaivro-main-session
blocked-by: [T04]
---

## Question

Chốt policy độ bền cho "resource có hạn, user vừa phải":
1. **Target load cụ thể**: "vừa phải" = bao nhiêu user đồng thời / message mỗi phút?
   (con số này quyết định mọi thứ phía dưới)
2. **Fallback chain**: Groq hết quota/chết → rơi về đâu (Ollama local? cache-only?
   "shop sẽ trả lời sau" + queue)? Thứ tự degrade: tính năng nào tắt trước
   (memory retrieval? escalation premium? clarify?)
3. **Retry/timeout budget** mỗi turn — khách chờ tối đa bao lâu trước khi nhận
   holding message?
4. **Backpressure**: quá tải thì xếp hàng ở đâu (Postgres queue có sẵn?) và khách
   thấy gì?

Blocked by **T04** (số liệu rate limit + menu pattern).

## Resolution

Chốt 2026-08-12 qua grilling với owner, trên nền số liệu T04
(`research/rate-limits-fallback.md`). Fact nền: router hiện KHÔNG có retry / timeout /
runtime-fallback (`cooldown_time: 0`); Groq không expose bộ đếm ngày trong header;
`queued_messages` + `support_queue` đã đúng hình dạng queue cần dùng.

1. **Target load (SLO thiết kế)**: **~10 user đồng thời, ~30 message/phút** — demo
   scale, khớp 1 uvicorn instance + pool 20. 70b chỉ dành cho ~20% turn khó (T08) nên
   TPD 100K đủ cho ngày demo. Con số này ghi thẳng vào spec làm tiêu chí eval load.
2. **Fallback chain — ladder A+B+D đầy đủ, RẼ THEO INTENT**:
   chain `Groq 70b → Groq 8b → Ollama local → cache-only → holding+queue`.
   - INFO/PRICING/SMALLTALK: được ăn mọi rung, kể cả cache-only.
   - ORDER/NEGOTIATION/COMPLAINT: KHÔNG nhận câu trả lời từ rung local/cache —
     rơi thẳng holding + queue → human (đúng tín hiệu degraded-là-20% của T07).
   - Thứ tự tắt tính năng: premium tool-loop (T08) tắt trước → escalation premium
     rơi về economy (cơ chế T064 sẵn có) → cuối cùng mới holding.
   - Mọi câu trả lời degraded phải tag `model_used` (đã có trong `QueryResponse`) —
     quality cliff không được im lặng.
3. **Retry/timeout budget**: **cap tổng ~30s/turn** rồi holding message. Timeout
   15s/call cloud, 25s local; tối đa 0–1 retry; **RateLimitError (429) KHÔNG retry**
   (429 free-tier kéo dài hàng giờ) — cooldown + nhảy fallback ngay. Per-exception
   retry policy của LiteLLM Router, không retry auth error.
4. **Backpressure**: semaphore in-process cap N turn LLM đồng thời; tràn → ghi
   `queued_messages` (drain bằng `FOR UPDATE SKIP LOCKED`, worker chia sẻ pool nên
   concurrency ≪ 20); thêm **token-budget gate app-side** — 1 row Postgres đếm
   token/model/ngày UTC, chạm ngưỡng (ví dụ 90%) → chủ động rơi rung sớm thay vì đâm
   tường 429. Khách thấy holding message và được trả lời khi drain. Zero infra mới,
   không Redis.

