# Research: Groq free-tier limits + LiteLLM resilience + graceful degradation (T04)

Date: 2026-08-11 · Scope: facts + pattern menu only. Policy decisions → T09.

## 1. Groq free-tier rate limits (current, Aug 2026)

Models this repo actually points at (per `core/ai_config.py` + `docs/deployment.md`
Option A, the current default):

- `groq/llama-3.3-70b-versatile` — `CHAT_MODEL` and the `premium-chat` alias
  resolution (first choice in `_litellm_params` when `GROQ_API_KEY` is set).
- `groq/llama-3.1-8b-instant` — documented `LIGHT_CHAT_MODEL` for the cloud option.
- Embeddings are **not** on Groq (fastembed in-process, ADR-006) — no Groq quota impact.

Free-plan limits (Groq console rate-limit docs, cross-checked Aug 2026):

| Model | RPM | RPD | TPM | TPD |
|---|---|---|---|---|
| llama-3.3-70b-versatile | 30 | 1,000 | 12,000 | **100,000** |
| llama-3.1-8b-instant | 30 | 14,400 | 6,000 | 500,000 |

Practical readings for this repo:

- **The 70B's binding constraint is TPD = 100K.** A RAG turn here (system + chunks +
  history) easily runs 2–4K tokens → the *day* is exhausted after roughly 25–50 heavy
  turns, long before RPM matters. RPD=1,000 is the second ceiling.
- The 8B has 14.4× the RPD and 5× the TPD — routing light work (normalization,
  intent, smalltalk — the repo's WP-V2-5 light-tier routing) to `llama-3.1-8b-instant`
  is the single biggest free-tier lever.
- On breach Groq returns **429** with a `retry-after` header (seconds); every response
  carries `x-ratelimit-remaining-{requests,tokens}` and `x-ratelimit-reset-*` headers.
  **Daily (RPD/TPD) counters are NOT exposed in headers** — daily burn must be tracked
  app-side (resets midnight UTC).
- Developer (paid) tier removes daily caps (llama-3.3-70B ≈ $0.59/M in + $0.79/M out) —
  out of scope for zero-cost, listed for completeness.

Sources:
- https://console.groq.com/docs/rate-limits (authoritative; per-account table)
- https://tokenmix.ai/blog/groq-free-tier-limits-2026
- https://www.grizzlypeaksoftware.com/articles/p/groq-api-free-tier-limits-in-2026-what-you-actually-get-uwysd6mb
- https://www.eesel.ai/blog/groq-pricing

⚠️ Limits are per-account, per-model and Groq adjusts them without notice — re-verify
in the console (`Settings → Limits`) at spec-lock time.

## 2. LiteLLM Router resilience features

Current repo state (`core/ai_config.py → LITELLM_CONFIG`): `routing_strategy:
"simple-shuffle"`, `cooldown_time: 0`, **no** `num_retries`, **no** `fallbacks`, **no**
`timeout`. The only "fallback" today is *static config-time* substitution (missing API
key → `settings.CHAT_MODEL`) — nothing handles a *runtime* 429/5xx/timeout.

What the Router offers (docs.litellm.ai/docs/routing, /docs/proxy/reliability):

| Knob | What it does | Recommended pattern here |
|---|---|---|
| `num_retries` + `retry_after` | Same-group retries; exponential backoff on 429, immediate on generic errors | 1–2 retries max; Groq's `retry-after` on free tier can be long — cap it, don't obey blindly |
| `timeout` / `stream_timeout` | Per-request hard timeouts | Set explicitly (e.g. 30s cloud / 60–120s local Ollama-CPU); today there is none → a hung call hangs the turn |
| `allowed_fails` + `cooldown_time` | After N fails/min, deployment pulled from rotation for `cooldown_time`s | e.g. `allowed_fails=2, cooldown_time=30`; current `cooldown_time: 0` disables cooldown entirely |
| `fallbacks` | Cross-model-group failover after retries exhaust: `fallbacks=[{"premium-chat": ["economy-chat"]}]` | This is the Groq → Ollama-local chain: register a `groq/...` deployment AND an `ollama/qwen3-4b-q6` deployment, fall back on 429/timeout |
| `default_fallbacks` | Catch-all chain for any group | Good safety net → last entry = local model |
| `context_window_fallbacks` / `content_policy_fallbacks` | Error-type-specific chains | Optional; context-window one is useful with small local ctx |
| `retry_policy` / `allowed_fails_policy` (per-exception) | e.g. `RateLimitErrorRetries=1`, `AuthenticationErrorRetries=0` | Don't retry auth errors; treat 429 as "cooldown + fallback", not "retry hard" |
| Same-group `order`/`weight` | Priority tiers inside one model_name | Alternative shape: put groq + ollama under ONE `model_name` with `order: 1/2` — failover without a separate fallback group |

Pitfalls (documented + community):

1. **Cooldown cascade**: if every deployment in a group cools down, calls fail with
   "No deployments available… try again in 60 seconds". Always keep a local deployment
   with `cooldown_time: 0` (per-deployment override in `model_info`) as the floor.
2. **Retry × fallback latency multiplication**: 2 retries × 30s timeout × 2-model chain
   = worst-case >2 min before the user hears anything. Budget: (retries+1)×timeout per
   link must fit the turn SLO; prefer *fewer retries, faster fallback*.
3. **429 ≠ transient on free tier**: a TPD/RPD 429 lasts hours — retrying is pure
   waste. Per-exception policy should send RateLimitError straight to fallback/cooldown.
4. **Multi-worker state**: in-memory cooldown/TPM tracking is per-process; LiteLLM's
   shared tracking assumes Redis. Single uvicorn instance (this repo) is fine; know
   the constraint before scaling workers.
5. **Quality cliff is silent**: falling back 70B → qwen3-4b changes answer quality with
   no signal. Tag `model_used` (already in `QueryResponse`) and consider a degraded-mode
   banner / stricter confidence threshold when serving from the fallback tier.

Sources:
- https://docs.litellm.ai/docs/routing
- https://docs.litellm.ai/docs/proxy/reliability
- https://docs.litellm.ai/docs/completion/reliable_completions
- https://deepwiki.com/BerriAI/litellm/7-reliability-and-resilience

## 3. Graceful degradation when the LLM is down — pattern menu

Ordered ladder as commonly practiced for customer-facing chatbots (each rung needs
the one below it):

| Pattern | What it serves | Requires | Trade-off |
|---|---|---|---|
| **A. Model fallback chain** (cloud→cloud→local) | Full answers, maybe worse | LiteLLM `fallbacks` + local model warm (Ollama/llama.cpp running) | Local qwen3-4b quality/latency ≪ 70B; must keep local weights pulled & `num_ctx` set (ADR-006 mitigations) |
| **B. Semantic-cache-only answers** | Exact/near-repeat questions | Embeddings must NOT depend on the dead provider — ✅ true here (fastembed in-process); repo already has `semantic_cache` + TTL | Only absorbs repeated queries (commonly 30–60% on FAQ-heavy sales traffic); stale-price risk bounded by `CACHE_TTL_SECONDS`; needs "serve-on-failure" mode that bypasses the normal threshold/TTL policy deliberately |
| **C. Retrieval-only / deterministic responses** | "Here's what I found:" top-k product chunks, price/stock lookups, rule-based intents | Retrieval path free of LLM calls — ⚠️ partially true here: pgvector search is LLM-free, but router/normalization nodes call the light model → need a bypass path (keyword/FTS route) | No synthesis, reads like search results; fine for product Q&A, useless for negotiation/complaints |
| **D. Holding message + queue** | "Đã nhận tin nhắn, sẽ phản hồi sớm" then process later or hand to human | A durable queue + a drain worker + (optionally) HITL notification — repo's `queued_messages` + `support_queue` are exactly this shape | Breaks conversational feel; needs dedup/ordering on drain and an SLA story; the safest floor for order/negotiation intents where a degraded LLM answer is riskier than silence |
| **E. Static "limited mode" notice** | Honest unavailability | Nothing | Last resort; still better than timeout silence |

Common production stack = **A + B + D**: chain models, serve cache hits during outage,
queue what can't be answered. C is a nice add-on where retrieval is already LLM-free.
Circuit-breaker state (LiteLLM cooldown effectively provides one) decides which rung
is active; log/flag every degraded answer.

Sources:
- https://futureagi.com/blog/what-is-llm-fallback-strategy-2026/
- https://markaicode.com/implement-graceful-degradation-llm-frameworks/
- https://www.buildmvpfast.com/blog/graceful-degradation-ai-agents-fallback-model-unavailable-2026
- https://www.groovyweb.co/blog/llm-integration-rate-limiting-caching-fallbacks-2026

## 4. Backpressure / queueing: single FastAPI + Postgres, no Redis

Consensus (2025–2026): **Postgres is a perfectly good queue at this scale.** With
`SELECT … FOR UPDATE SKIP LOCKED`, a single instance sustains tens of thousands of
jobs/hour (benchmarks: ~50K jobs/hour comfortable; DBOS shows far higher with tuning)
— orders of magnitude above a "moderate user count" demo. Redis buys ~3–4× throughput
you don't need, at the cost of a second stateful service.

Options menu:

| Option | How | Trade-off |
|---|---|---|
| **In-process semaphore** (asyncio.Semaphore around LLM calls) | Cap concurrent LLM turns at N; excess waits or gets holding message | Zero infra; lost on restart; per-process only — fine for 1 uvicorn instance |
| **Postgres table queue + SKIP LOCKED worker** | Insert message row; asyncio background task drains with `FOR UPDATE SKIP LOCKED`; optional `LISTEN/NOTIFY` to avoid poll latency | Durable, transactional with business data, survives restart; needs a worker loop + poll interval or NOTIFY wiring |
| **Token-budget gate (app-side Groq accounting)** | Count tokens/requests per model per UTC day in a Postgres row (Groq doesn't expose RPD/TPD in headers); pre-check before calling; switch to degraded mode near budget | Only way to degrade *before* the 429 wall; approximate token counting is fine |
| **Edge rate-limit (429 at reverse proxy)** | Per-chat / per-IP limits at Nginx/Traefik, per `docs/deployment.md` Week-7 note | Protects the ack path; blunt — no queue, user just gets rejected |

What the existing tables already support (`models/schema.py`):

- **`queued_messages`** (`session_id`, `message_text`, `received_at`, `processed`,
  `archived`, index on `(session_id, processed, received_at)`): built for messages
  arriving while a session is escalated — the same shape works for **LLM-unavailable
  buffering** (pattern 3-D). Ready for a per-session ordered drain. Missing for a
  general work queue: a `status`/`attempts`/`locked_at` column (or just rely on
  `FOR UPDATE SKIP LOCKED` + `processed=false`), and a NOTIFY trigger if poll latency
  matters.
- **`support_queue`** (`session_id` unique, `reason`, `status='pending'`,
  `assigned_to`, `context_snapshot` JSONB): a human-handoff queue with slot-per-session
  semantics — right home for "LLM down → escalate to human with context snapshot"
  (add a reason like `llm_unavailable`).
- Caveats: `DATABASE_POOL_SIZE=20, MAX_OVERFLOW=0` — a drain worker shares this pool;
  keep worker concurrency ≪ pool size. Checkpointer uses psycopg3 separately.

Sources:
- https://www.dbpro.app/blog/postgresql-skip-locked
- https://medium.com/@harsh.vaghela.work/postgres-is-the-only-queue-you-need-until-50k-jobs-sec-5931611b551c
- https://spin.atomicobject.com/redis-postgresql/
- https://www.dbos.dev/blog/making-postgres-queues-scale
- https://dev.to/software_mvp-factory/postgresql-listennotify-as-a-lightweight-job-queue-replacing-redis-for-your-startups-background-4g8j

## Key numbers at a glance

- Groq free, 70B: **30 RPM / 1K RPD / 12K TPM / 100K TPD** → ~25–50 heavy RAG turns/day.
- Groq free, 8B-instant: **30 RPM / 14.4K RPD / 6K TPM / 500K TPD**.
- 429 gives `retry-after`; **no daily counters in headers** → app-side budget tracking required.
- Repo router today: no retries, no timeout, no runtime fallback, cooldown disabled.
- Postgres SKIP-LOCKED queue: ~50K jobs/hour on one instance — no Redis needed at demo scale.
