# Proposal: v2-5-cost-dashboard (WP-V2-5 — trục TIẾT KIỆM)

**Type**: cr (fast-track — implement theo chỉ đạo trực tiếp của user, spec delta ghi lại kèm
kết quả đo; xem `docs/upgrade-plan-v2.md` §WP-V2-5)
**Date**: 2026-07-31 · **Branch**: `feature/V2-5-cost-dashboard`

## Problem

1. **Số liệu cost THẬT đã có nhưng vô hình.** `model_traces` nhận token/cost/latency thật từ
   `extract_llm_metrics` (WP3) — nhưng SME không có cách nào xem tổng chi phí theo ngày /
   khách / model ngoài việc tự viết SQL.
2. **Không có trần chi phí.** Khi SME gắn cloud key (Groq/Gemini/OpenAI), một ngày lưu lượng
   đột biến — hoặc MỘT khách spam — có thể đốt ngân sách không giới hạn. Không guard nào chặn.
3. **SMALLTALK chạy model đắt vô ích.** Lời chào "xin chào" đi thẳng answer_node với
   `economy-chat` (qwen3-1.7b) trong khi `light-chat` (qwen3-0.6b) trả lời tốt với chi phí
   nhỏ hơn nhiều.

## Solution

1. **Cost dashboard** — `GET /admin/costs?from=&to=&group_by=day|customer|model` (router
   `/admin`, sau `verify_admin_key`): aggregate `model_traces` → calls, tokens in/out/total,
   cost USD, latency p50/p95 (`percentile_cont`), cache hits + hit-rate. JSON thuần — SME xem
   bằng curl/sheet, không cần UI. Mặc định 7 ngày gần nhất (UTC). `group_by=customer` dựa
   trên `metadata->>'customer_id'` — answer_node từ V2-5 stamp customer_id/session_id/intent
   vào JSONB metadata của MỌI trace (không cần migration; rows cũ nhóm "unknown").
2. **Budget guard** (`services/costs.py::check_budget`, gọi đầu Path 3 của answer_node —
   đường duy nhất tốn token):
   - `DAILY_COST_LIMIT_USD` (default 0 = off): tổng cost hôm nay (UTC) chạm trần → ép
     `light-chat` + warning log + `budget_downgrade=true` trong trace metadata + tắt cascade
     premium reserve của turn đó. KHÔNG BAO GIỜ chặn trả lời — chỉ hạ cấp.
   - `CUSTOMER_DAILY_MSG_CAP` (default 0 = off): 1 khách vượt N lượt-LLM/ngày → tin nhắn
     lịch sự hẹn quay lại (không gọi LLM), trace `CUSTOMER_CAP`. Cache-hit / business /
     decline không tính (miễn phí).
   - Cả hai guard: default 0 → zero query thêm; DB lỗi → fail OPEN (đồng hồ hỏng không được
     chặn bán hàng).
3. **Routing tune**: SMALLTALK → `light-chat` (audit: trước đó rơi mặc định `economy-chat`;
   không có retrieval context, không cần reasoning). Kill switch
   `CHEAP_INTENT_LIGHT_ROUTING=false` → hành vi cũ.

## Measured results

| Metric | Before | After |
|---|---|---|
| Unit suite | 457 pass | 472 pass (15 test mới: guard matrix + report + answer-path) |
| Tier-R recall@k | 34/34 (100%) | 34/34 (100%) — Δ 0.0pp |
| Tier-F smoke (--rerun --flush-cache, 8b-instant) | 12/12 (100%) | 12/12 (100%) — Δ 0.0pp |
| Xem chi phí | tự viết SQL | 1 lệnh curl `/admin/costs` — verify khớp raw SQL dev DB (60 calls / 31,128 tokens / $0 local) |
| Trần chi phí ngày | không có | chạm trần → hạ light-chat (unit-verified) |
| 1 khách spam | đốt ngân sách không giới hạn | quá cap → tin nhắn hẹn lại, 0 LLM call (unit-verified) |
| SMALLTALK | economy-chat (1.7b) | light-chat (0.6b) |
| Endpoint auth | — | 401 thiếu key · 400 group_by sai · 200 kèm data (ASGI-verified) |

> Ghi chú đo: Tier-R/Tier-F đi qua `hybrid_search_rrf`/`answer_with_rag` — không qua
> answer_node graph path, hai gate là regression guard (flat = đúng kỳ vọng). Hành vi V2-5
> nằm ở graph path + admin API — đo bằng 15 unit test + ASGI smoke + đối chiếu raw SQL.
> group_by=customer chỉ có dữ liệu từ V2-5 trở đi (trace cũ thiếu customer_id → "unknown").

## Kill switch / rollback

Mặc định đã là off: `DAILY_COST_LIMIT_USD=0` + `CUSTOMER_DAILY_MSG_CAP=0` → guard không chạy
query nào. `CHEAP_INTENT_LIGHT_ROUTING=false` → SMALLTALK về economy-chat. Không migration,
không đổi graph topology (`GRAPH_SCHEMA_VERSION` giữ "006"). Rollback code = revert commit.
