---
id: T02
title: "Research: Small-Model Tool-Calling Reliability (zero-cost tier)"
type: wayfinder:research
mode: AFK
status: closed
assignee: research-subagent
blocked-by: []
---

## Question

Với ràng buộc zero-cost (Groq free tier + model local cỡ qwen3-4b), tool-calling loop
có đủ tin cậy để thay thế / bổ sung intent-enum router hiện tại không? Cần fact:
(1) các model trên Groq free tier hỗ trợ tool-calling ở mức nào, error rate thực tế
với 5-8 tools tiếng Việt; (2) qwen3-4b-class local models tool-call qua
LiteLLM/Ollama ổn không; (3) pattern hybrid phổ biến (tool-calling cho hội thoại,
state machine cho giao dịch) yêu cầu gì ở model. Output: findings tại
`wayfinder/research/small-model-tool-calling.md` với khuyến nghị feasible /
not-feasible / feasible-with-guardrails. Quyết định cuối thuộc ticket T08.

## Resolution

**Verdict: feasible-with-guardrails** — chi tiết + nguồn tại
`wayfinder/research/small-model-tool-calling.md`.
Tool-calling loop chỉ nên **bổ sung** (không thay thế) intent-enum router, giới hạn ở
mặt tư vấn read-only. Driver là Groq free tier (`llama-3.3-70b-versatile` — tools +
parallel calls, ~30 RPM/12K TPM/1K req-day, cached tokens miễn rate-limit); qwen3-4b
local chỉ làm fallback single-shot: BFCL single-turn ~75-82% nhưng multi-turn sụp còn
~35% nên graph phải giữ loop (tối đa 1-2 tool hop/turn). LiteLLM native `ollama/` path
có chuỗi bug tool-calling còn mở — bắt buộc route qua OpenAI-compatible endpoint
(repo đã có sẵn pattern `hosted_vllm/`). Tool schema giữ tiếng Anh; Pydantic-validate
mọi call + 1 retry; giao dịch/order/HITL giữ nguyên state machine. 8 guardrail cụ thể
(G1-G8) ghi trong findings; quyết định cuối thuộc T08.
