---
label: wayfinder:research
ticket: T02
title: "Small-Model Tool-Calling Reliability (zero-cost tier)"
date: 2026-08-11
---

# Small-Model Tool-Calling Reliability under the Zero-Cost Constraint

Research question: with Groq free tier + local qwen3-4b-class models (LiteLLM/Ollama), is a
tool-calling agent loop reliable enough to **replace or augment** the current intent-enum router
of a Vietnamese e-commerce sales agent?

**Short answer: feasible-with-guardrails** — as an *augmentation* (tool loop for consultation,
Groq-hosted model as driver, local 4B as degraded fallback), **not** as a replacement for the
enum router or the transactional state machine. Details and named guardrails below.

---

## 1. Groq free tier: tool-calling support & reliability

### What's available (as of mid-2026)

- **All Groq-hosted models support tool use.** Models with **parallel tool calling**:
  `llama-3.3-70b-versatile`, `llama-3.1-8b-instant`, `qwen/qwen3.6-27b`,
  `minimaxai/minimax-m2.7`. The `openai/gpt-oss-20b/120b` models support tools + JSON mode but
  **not** parallel tool calls. `groq/compound(-mini)` only run Groq's built-in tools — not usable
  for our custom tools. (Groq tool-use docs)
- Groq's docs recommend "the latest models for improved tool use capabilities"; Llama 3.3 70B and
  the Qwen models have the most complete support (parallel calls, JSON mode).
- **Free-tier limits** (no credit card, per-model, mid-2026 figures): roughly
  **30 RPM / 6-12K TPM / ~1K requests-day** for `llama-3.3-70b-versatile` (30 RPM, 12K TPM,
  100K TPD, 1K RPD); `qwen/qwen3-32b` at 60 RPM but only 6K TPM; `gpt-oss-120b` at 30 RPM,
  8K TPM, 200K TPD. Baseline across models is quoted as 30 RPM / 6K TPM / 14.4K req/day.
  **Cached tokens don't count toward rate limits** — a stable system prompt + tool schema block
  stretches the free tier significantly.
- Implication for a loop: a consultation turn that costs 2-3 LLM calls (plan -> tool -> answer)
  fits comfortably inside 30 RPM at demo scale ("user vừa phải"), but the **TPM ceiling** is the
  real constraint: 5-8 tool JSON-schemas plus history can easily be 2-4K tokens/call, so 12K TPM
  is roughly 3-5 calls/minute sustained. Budget ~1 tool-loop turn per customer per 20-30s at peak.

### Reliability with 5-8 tools

- No public error-rate numbers exist for exactly "5-8 tools + Vietnamese input". Proxy data:
  70B-class Llama on BFCL-style single/parallel tool selection is solidly usable in production
  agents (this is the class Groq itself recommends for agentic tool use), while everything at
  <=8B/4B degrades sharply on **multi-turn** tool use (see section 2). 5-8 tools is a small tool
  set by BFCL standards and is the easy end of the distribution — tool *selection* is not the
  risk; multi-turn *state keeping* and argument fidelity are.
- Vietnamese: research on multilingual function calling consistently finds degradation vs
  English, and multilingual FC benchmarks remain sparse (Ticket-Bench, arXiv 2509.14477, was
  built precisely because regionalized agent evaluation was missing). Standard mitigation:
  **keep tool names, descriptions and JSON schemas in English**; only the user utterance is
  Vietnamese. Argument values that copy Vietnamese free text (product names, addresses) are
  reliable; argument values that require normalization (dates, quantities, "cái này/cái kia"
  anaphora) are where small models fail — validate them.

Sources:
- https://console.groq.com/docs/tool-use
- https://community.groq.com/t/do-models-hosted-on-groqcloud-support-function-calling-tool-use/44
- https://tokenmix.ai/blog/groq-free-tier-limits-2026
- https://pricepertoken.com/endpoints/groq/free
- https://costbench.com/software/llm-api-providers/groq/free-plan/
- https://arxiv.org/pdf/2509.14477 (Ticket-Bench — multilingual/regionalized agent eval)

## 2. qwen3-4b-class local models via LiteLLM + Ollama

### Measured capability

- **Qwen3-4B on BFCL: ~62% overall** (prompt-mode), ~75-82% on live/non-live single-turn — but
  **multi-turn collapses to ~35%** (base), i.e. roughly 2 of 3 multi-turn tool episodes go wrong.
  RL-fine-tuned variants (FISSION-GRPO, EGPO) push multi-turn to ~41-56%, still far from
  production-loop territory. (arXiv 2508.05118, 2601.15625)
- Community consensus (2026): **qwen3:8b is the smallest Ollama model that tool-calls
  consistently** in agent testing; 4B is usable for *single-shot* structured extraction /
  one-tool dispatch, not for autonomous loops. (promptquorum.com, insiderllm.com)

### LiteLLM + Ollama plumbing: known issues

Recurring, still-open class of bugs in LiteLLM's native `ollama/` / `ollama_chat/` transforms:

- BerriAI/litellm#7570 — broken Ollama completion transformation for tool calling (500s).
- BerriAI/litellm#11104 — `ollama_chat/` models return no tool_calls via LiteLLM though Ollama's
  own API works.
- BerriAI/litellm#24091 (Mar 2026) — Ollama returns valid `tool_calls` but LiteLLM drops them
  (`message.tool_calls` is `None`).
- BerriAI/litellm#26094 / ollama/ollama#15719 (Apr 2026) — LiteLLM's model-info DB marks some
  Ollama models `supports_function_calling: false`, silently switching to a prompt-injection
  fallback -> malformed tool-result handling / infinite tool loops.
- ollama/ollama#14493 — Ollama-side template bugs (tool calls emitted inside unclosed
  `<think>` blocks).

**Standard workaround, and good news for this repo**: route through **Ollama's OpenAI-compatible
`/v1` endpoint** using an `openai/<model>` (or `hosted_vllm/`) model string with `api_base`
pointed at Ollama — this bypasses LiteLLM's fragile native Ollama transform and tool calls work.
This repo *already* does exactly this pattern for llama-server (`core/ai_config.py`
`_litellm_params`: `hosted_vllm/...` -> `LLAMA_SERVER_BASE_URL`), so the workaround is one config
convention away: **never use `ollama/` model strings for tool-calling paths**; reserve
`ollama/` for embeddings (`bge-m3`), route chat through the OpenAI-compatible path.

Sources:
- https://github.com/BerriAI/litellm/issues/7570 · /11104 · /24091 · /26094 · /19742 · /5617
- https://github.com/ollama/ollama/issues/14493 · /15719
- https://arxiv.org/html/2508.05118v4 (Qwen3-4B BFCL numbers)
- https://arxiv.org/pdf/2601.15625 (Fission-GRPO, 4B multi-turn recovery)
- https://www.promptquorum.com/local-llms/top-open-source-models-ollama
- https://insiderllm.com/guides/function-calling-local-llms/

## 3. 2026 hybrid patterns and minimal model requirements

The dominant production pattern in 2026 is exactly the hybrid this ticket hypothesizes:

- **Explicit workflow graph / state machine for anything transactional.** Industry has shifted
  away from open-ended agent loops toward explicit state machines (LangGraph nodes = tool/LLM
  steps, edges = permitted transitions) for flows with side effects. Orders, drafts, payment,
  cancellation stay on deterministic rails.
- **HITL interrupt before real-world side effects.** The canonical safe-agent pattern: agent
  *proposes* a tool call (place order, send message), a LangGraph `interrupt` + checkpointer
  pauses for human approval, `thread_id` resumes after review. This repo already has the whole
  substrate (Postgres checkpointer, hitl_guard_node, risk tiers).
- **Tool-calling loop only for the read-only consultation surface** (search catalog, check
  price/stock, fetch policy, retrieve memory). Failure mode there is a wrong/failed lookup —
  recoverable — not a corrupted order.

**Minimal model capability the hybrid needs** (much lower than a full autonomous agent):
1. Pick 1 correct tool out of 5-8 given an English schema block — 4B-class OK (~75-82%
   single-turn live BFCL), 70B-class comfortable.
2. Emit schema-valid JSON args — enforce with response-format/JSON mode + Pydantic validation +
   one repair retry; do NOT trust raw output.
3. It does **not** need reliable multi-turn tool chaining if the graph, not the model, owns the
   loop: cap at 1-2 tool hops per turn and re-enter through the router each turn.

Sources:
- https://activewizards.com/blog/architecting-event-driven-conversational-agents-with-langgraph/
- https://medium.com/data-science-collective/architecting-human-in-the-loop-agents-interrupts-persistence-and-state-management-in-langgraph-fa36c9663d6f
- https://towardsdatascience.com/building-human-in-the-loop-agentic-workflows/
- https://www.elastic.co/search-labs/blog/human-in-the-loop-hitllanggraph-elasticsearch

## 4. Current model slots in this repo (core/ai_config.py, core/config.py)

| Slot | Default | Notes |
|---|---|---|
| `light-chat` (`LIGHT_CHAT_MODEL`) | `hosted_vllm/economy-chat` (local llama-server) | normalization/keyword; SMALLTALK routing (WP-V2-5) |
| `economy-chat` (`CHAT_MODEL`) | `hosted_vllm/economy-chat` | main RAG/chat tier; universal fallback when cloud keys missing |
| `powerful-chat` (`POWERFUL_CHAT_MODEL`) | `hosted_vllm/economy-chat` | deep-reasoning tier (alias `premium-local-chat`) |
| `premium-chat` (`PREMIUM_MODEL`) | -> `groq/llama-3.3-70b-versatile` if `GROQ_API_KEY`, else gemini-2.5-flash / gpt-4o-mini / local | escalation tier — **already the natural tool-loop driver** |
| `qwen3-4b` alias | `ollama/qwen3-4b-q6` | WARNING: uses the buggy `ollama/` LiteLLM path — switch to OpenAI-compatible routing before tool-calling on it |
| `economy-embedding` (`EMBED_MODEL`) | `ollama/bge-m3` (1024-dim) | embeddings only, unaffected |

Missing key => automatic fallback to `CHAT_MODEL` (local), so a Groq-driven tool loop degrades to
local silently — the design must stay correct (if less smart) under that degradation.

## 5. Recommendation: **feasible-with-guardrails**

A tool-calling loop can **augment** (not replace) the intent-enum router, restricted to the
read-only consultation surface, with these named guardrails:

- **G1 — Driver on Groq, not on 4B.** The tool-loop planner runs on `premium-chat` ->
  `groq/llama-3.3-70b-versatile` (tools + parallel calls, free tier). Local qwen3-4b is the
  *degraded* fallback and is then restricted to single-shot, one-tool dispatch per turn.
- **G2 — Read-only tool surface.** Loop tools are consultation-only (catalog search,
  price/stock, policy, memory). Any mutating action (draft order, order ops, handoff) is not a
  loop tool — it exits into the existing LangGraph state machine + hitl_guard/risk tiers.
- **G3 — Graph owns the loop, not the model.** Max 1-2 tool hops per user turn, then forced
  answer; re-route every turn. This sidesteps the 4B multi-turn collapse (~35% BFCL multi-turn).
- **G4 — Schema validation + single repair retry.** Pydantic-validate every tool call; on
  failure, one retry with the validation error appended; on second failure, fall back to the
  intent-enum router path. JSON mode on where supported.
- **G5 — English tool schemas, Vietnamese content.** Tool names/descriptions/enums in English;
  never require the model to translate or normalize Vietnamese into arguments without
  validation (dates, quantities, anaphora are the failure hot-spots).
- **G6 — Keep the intent-enum router as fast-path and fallback.** It stays the first hop
  (cheap, deterministic, works on light tier) and the guaranteed path when Groq is
  rate-limited/keyless. The loop is an escalation for intent-gap / ambiguous consultation turns.
- **G7 — LiteLLM plumbing rule.** Tool-calling models must NOT use `ollama/`-prefixed strings —
  use the OpenAI-compatible route (`hosted_vllm/` to llama-server, or `openai/` +
  `api_base=<ollama>/v1`), which the repo's `_litellm_params` already supports. Fix the
  `qwen3-4b` alias accordingly.
- **G8 — Rate-limit budget.** <=3 Groq calls per turn; rely on prompt caching (stable system +
  tool block; cached tokens don't count toward rate limits); on 429, degrade to the G1 fallback
  rather than queueing.

**Not feasible**: replacing the transactional/order flow or HITL gates with a free-running
tool-calling agent on this model class — 4B multi-turn tool reliability (~35-56%) and Groq
free-tier TPM ceilings both rule it out.

Final decision belongs to ticket T08.
