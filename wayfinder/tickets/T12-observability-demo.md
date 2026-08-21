---
id: T12
title: Observability mức demo — đo deflection rate & alert handoff
type: wayfinder:grilling
mode: HITL
status: closed
assignee: thaivro-main-session
blocked-by: []
---

## Question

Delegation Contract (T07) đã chốt metric: **deflection rate theo session ≥ 80%**, đo
từ `support_queue` + `interrupted_sessions`. Chốt phần hiển thị/alert mức demo:
1. Deflection rate + queue depth trình bày ở đâu — Phoenix traces đã có; thêm endpoint
   stats dưới `/admin`, hay một dashboard nhỏ?
2. Alert gì khi handoff — Telegram notify admin đã có; có cần alert khi queue quá N
   item hoặc case chờ quá X phút (liên quan gap O28 proactive alert)?
3. Log structured tối thiểu nào để demo "kể chuyện" 80/20 (tín hiệu escalate nào bắn,
   tier nào, kết cục ra sao)?

## Resolution

Chốt 2026-08-12 qua grilling với owner. Fact nền: Phoenix traces đã chạy (logfire owns
TracerProvider + OTLP export, `core/logging.py`); `/admin/stats` + `/admin/costs` +
`/hitl/pending` đã tồn tại; Telegram notify admin per-case đã có
(`services/hitl/telegram_service.py`); `timeout_scheduler` đã có loop định kỳ tái dụng được.

1. **Hiển thị — endpoint stats + mini dashboard tĩnh**: thêm 1 endpoint JSON dưới
   `/admin` trả deflection rate theo session (tính từ `support_queue` +
   `interrupted_sessions`, đúng công thức T07), queue depth, và bộ đếm degraded turn
   (từ tag `model_used`, nối T09). Kèm 1 trang HTML tĩnh nhỏ trên khung
   `ui.py`/`api/static` sẵn có đọc endpoint đó — công cụ "kể chuyện" khi demo CV.
   Phoenix giữ vai trò trace/debug, không gánh metric.
2. **Alert — bộ đủ 3 loại qua Telegram admin sẵn có**, chạy trên loop
   `timeout_scheduler` tái dụng:
   - queue depth > N item;
   - case chờ trong queue > X phút (đóng gap O28 proactive alert);
   - degrade event: rơi rung fallback ladder (429 / token-budget gate trip — nối T09).
   N và X là config (default đề xuất mức demo: N=5, X=10 phút — tinh chỉnh lúc build).
   Notify per-case khi handoff giữ nguyên như hiện tại.
3. **Log structured — span attributes trên trace sẵn có, KHÔNG bảng mới**: mỗi turn
   gắn attrs chuẩn vào span Phoenix đang có: `intent`, `model_used`/tier,
   `risk_signals` đã bắn (composite ∨ NEGOTIATION/COMPLAINT ∨ clarify≥2 ∨ degraded —
   đúng 4 tín hiệu 20% của T07), `outcome`
   (self-handled / handoff / declined / queued). Deflection rate KHÔNG đọc từ trace —
   tính từ bảng Postgres sẵn có như T07 đã chốt; trace chỉ phục vụ kể chuyện per-turn.
   Zero infra mới, zero bảng mới, 1 write/turn không phát sinh.
