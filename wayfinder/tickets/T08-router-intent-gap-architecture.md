---
id: T08
title: Router & Intent-Gap Architecture — enum router vs tool-calling vs hybrid
type: wayfinder:grilling
mode: HITL
status: closed
assignee: thaivro-main-session
blocked-by: [T02, T03]
---

## Question

Quyết định kiến trúc phần "nửa trên" của graph (router → retrieval → confidence):
1. Giữ intent-enum router, chuyển sang tool-calling loop, hay hybrid (tool-calling
   cho tư vấn, state machine giữ nguyên cho order/HITL)? — dựa trên findings T02.
2. Xử lý **intent flip 180° giữa hai câu liền nhau** bằng kỹ thuật nào (theo menu
   T03): history-aware classification, sticky intent + shift detection, hay query
   rewrite? Chi phí thêm bao nhiêu call/turn?
3. Confidence gating giữ embedding-similarity threshold hay đổi cơ chế (LLM
   self-report, judge sau generate) — trong ngân sách zero-cost?
4. Cái gì BỚT được: node/call nào thừa trên đường 80% case phổ thông (router LLM call
   có cắt được cho câu chào hỏi/FAQ nhờ cache/keyword không)?

Blocked by **T02** (tool-calling feasibility) và **T03** (intent-tracking menu).

## Resolution

Chốt bởi user 2026-08-11 (3/4 câu; câu 4 tách ra ticket T11):

1. **Kiến trúc: Hybrid escalation.** Router enum + fast-path giữ nguyên làm đường mặc
   định cho ~80% case (model nhỏ/8b). Tool-calling loop trên Groq `llama-3.3-70b`
   CHỈ kích hoạt cho turn tư vấn mơ hồ/intent-gap (~20% case), tuân thủ guardrails
   G1–G8 của T02 (tool read-only, graph giữ loop ≤2 hop, Pydantic-validate + 1 retry,
   schema tiếng Anh, ≤3 Groq call/turn, 429 → fallback local). Order/HITL giữ nguyên
   state machine. Khớp budget 100K TPD của 70b (T04) và triết lý 80/20.
2. **Intent-flip: full combo T03, 0 LLM call thêm/turn.** Router đọc 3 turn cuối +
   `previous_intent` từ checkpointed state; `IntentClassification` thêm cờ
   `intent_shift`; transition table thuần Python (ORDER→CANCEL là chuyển tiếp mong
   đợi; nhảy lạ cần confidence ≥0.7, hysteresis giữ <0.5); `make_initial_state`
   ngừng wipe `intent`/`secondary_intents`; keyword fast-path mở rộng với tín hiệu
   do dự ("thôi", "để xem thêm", "khoan đã"…). Query rewriting: DEFER.
3. **Confidence gating: giữ embedding-similarity threshold + tinh chỉnh.** Không thêm
   call; `intent_shift`/`previous_intent` tham gia quyết định route; borderline ưu
   tiên clarify_node hơn escalate. LLM-judge sau generate: không làm.
4. **Cắt giảm trên đường 80%**: CHƯA quyết — user muốn phân tích tác động kỹ trước.
   Tách thành ticket T11 (impact analysis 3 ứng viên cắt: keyword fast-path
   SMALLTALK, semantic-cache trước router, skip memory node có điều kiện). Quyết
   định cắt sẽ chốt khi lắp spec (T10, giờ block thêm bởi T11).

Ngân sách call/turn suy ra từ (1)+(2)+(3): đường 80% = 1 router call + 1 answer call
(như hiện tại); đường 20% = +≤2 tool hop trên 70b, trần ≤3 Groq call/turn (G8).
