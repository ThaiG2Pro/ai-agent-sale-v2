---
id: T03
title: "Research: Multi-turn Intent Tracking cho model nhỏ (intent flip 180°)"
type: wayfinder:research
mode: AFK
status: closed
assignee: research-subagent
blocked-by: []
---

## Question

Router hiện classify từng turn độc lập (stateless) → hai câu liền nhau ý định đảo
chiều 180° ("đặt cái này đi" → "thôi để xem thêm") dễ bị xử lý sai. Kỹ thuật nào rẻ
đủ chạy trên model nhỏ để bám intent qua nhiều turn: (1) classify kèm N turn history
trong prompt — chi phí/độ chính xác; (2) query rewriting / coreference resolution
trước router; (3) sticky intent state + explicit shift-detection (chỉ đổi intent khi
có tín hiệu rõ); (4) cách các framework 2026 xử lý (LangGraph memory channels,
conversation state). Repo đã có `secondary_intents` + checkpointer state — tận dụng
được gì. Output: `wayfinder/research/multiturn-intent-tracking.md` — bảng kỹ thuật ×
chi phí (LLM call thêm/turn) × độ phức tạp. Quyết định cuối thuộc ticket T08.

## Resolution

Findings: `wayfinder/research/multiturn-intent-tracking.md` — comparison of 4 techniques
(history-in-prompt, pre-router query rewriting, sticky intent state machine, LangGraph state
facilities) by extra LLM calls/turn × complexity × robustness, with sources. Recommendation:
combine (1)+(3)+(4) at ZERO extra LLM calls/turn — router reads last 3 turns + previous_intent
from checkpointed state, `IntentClassification` gains `intent_shift`, a deterministic
transition table (ORDER_PLACEMENT→CANCEL is an expected transition) decides stick-vs-switch,
and `make_initial_state` stops wiping `intent`/`secondary_intents`. Extend the CANCEL keyword
fast-path with hesitation signals ("thôi", "để xem thêm", …). Defer query rewriting (+1
call/turn, negation-loss risk on small models) unless retrieval on elliptical follow-ups also
degrades. Final architecture decision belongs to T08.
