---
id: T04
title: "Research: Groq free-tier limits + LiteLLM retry/fallback/degradation patterns"
type: wayfinder:research
mode: AFK
status: closed
assignee: research-subagent
blocked-by: []
---

## Question

Độ bền với resource có hạn: cần fact về (1) rate limit / TPM / RPD thực tế của Groq
free tier hiện hành (2026) cho các model đang dùng trong `core/ai_config.py`;
(2) LiteLLM router: retry, cooldown, fallback chain (Groq → Ollama local), timeout
best practices; (3) pattern degrade gracefully khi LLM chết: trả lời từ semantic
cache / retrieval-only / xếp hàng chờ — cái nào phổ biến cho chatbot bán hàng;
(4) backpressure với lượng user vừa phải trên 1 instance FastAPI + Postgres (không
Redis). Output: `wayfinder/research/rate-limits-fallback.md` — số liệu limit cụ thể +
menu pattern kèm trade-off. Quyết định policy thuộc ticket T09.

## Resolution

Findings: `wayfinder/research/rate-limits-fallback.md` (2026-08-11, web + repo verified).
Gist: Groq free tier — llama-3.3-70b-versatile: 30 RPM / 1K RPD / 12K TPM / **100K TPD**
(TPD is the binding limit, ~25–50 heavy RAG turns/day); llama-3.1-8b-instant: 30 RPM /
14.4K RPD / 6K TPM / 500K TPD. Repo router today has NO retries/timeout/runtime fallback
(`cooldown_time: 0`); LiteLLM provides `num_retries`/`timeout`/`allowed_fails+cooldown`/
`fallbacks` for a Groq→Ollama-local chain — pitfalls: cooldown cascade, retry-latency
multiplication, "429 on daily caps is not transient". Degradation menu: fallback chain →
semantic-cache-only (embeddings are LLM-provider-independent here) → retrieval-only →
holding message + queue (`queued_messages`/`support_queue` fit as-is) → limited-mode
notice. Backpressure: Postgres SKIP-LOCKED queue + app-side daily token-budget gate
(Groq exposes no RPD/TPD headers) suffice without Redis. Policy choice → T09.
