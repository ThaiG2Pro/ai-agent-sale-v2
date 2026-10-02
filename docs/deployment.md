# Docker Deployment Guide

## Prerequisites

- Docker Engine + Docker Compose plugin
- A DB password file at `./secrets/db_password.txt` (Docker secret `db_password`)
- `.env` file copied from `.env.example`

Password resolution in the app: `DB_PASSWORD` env → the mounted secret file
(`DB_PASSWORD_FILE`, default `/run/secrets/db_password`) → dev default `password`.
Inside compose both `db` and `api` mount the secret, so leave `DB_PASSWORD` empty
there. For host-run commands (`alembic`, `scripts/*`, `uvicorn`) set
`DB_PASSWORD` in `.env` to the file's content.

## Environment Variables

Use `.env.example` as baseline, then set:

- `DB_USER`, `DB_NAME`, `DB_PORT`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_WEBHOOK_URL`
- `DATABASE_POOL_SIZE=20`, `DATABASE_MAX_OVERFLOW=40` (compose maps these to
  `DB_POOL_SIZE` / `DB_MAX_OVERFLOW`; app dev defaults are 10 / 20)

Optional explicit DSN format:

- `DATABASE_URL=postgresql+asyncpg://<user>:<password>@<host>:<port>/<db>`

> `TELEGRAM_WEBHOOK_SECRET` has **no default** in `docker-compose.yml` — compose
> refuses to start until you set it (env or `.env`, min 20 chars).

## LLM Provider Options

Model settings (`LIGHT_CHAT_MODEL`, `CHAT_MODEL`, `POWERFUL_CHAT_MODEL`,
`EMBED_MODEL`) accept **any LiteLLM model string**. LiteLLM reads provider API
keys straight from the environment; `docker-compose.yml` passes them through
(`GEMINI_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, `ANTHROPIC_API_KEY`).

> **Which backend for which deployment?** See
> [ADR-006](adr/006-model-provider-and-embedding-runtime.md) — the decision
> table (Ollama for dev, llama.cpp for SME self-host, vLLM for GPU
> concurrency, cloud API for zero-ops) and the mandatory Ollama mitigations
> (`num_ctx`, exact quant tags).

### Option A — Cloud chat + local embeddings (the `.env.example` default)

No Ollama needed at all; embeddings run in-process (fastembed ONNX, CPU):

```bash
# .env
CHAT_MODEL=groq/llama-3.3-70b-versatile
LIGHT_CHAT_MODEL=groq/llama-3.1-8b-instant
GROQ_API_KEY=<your key>
EMBED_MODEL=local/multilingual-e5-large   # fastembed, in-process
```

### Option B — Fully local chat (Ollama on the host)

The `api` container runs with `network_mode: host`, so host Ollama is simply
`OLLAMA_BASE_URL=http://localhost:11434` (Profile 1 in `.env.example`; the
compose fallback points at a llama-server on `:8080`, so set it explicitly).
Set `num_ctx` explicitly and pin exact quant tags — see ADR-006.

### ⚠️ Embedding model / dimension constraint

pgvector columns are `Vector(1024)`. Only switch `EMBED_MODEL` to a model
emitting **1024-dim vectors**; a mismatched dimension fails fast at the AI
gateway with "Configuration Error: Model Mismatch". More importantly:
**changing the embedding model (or the exact-pinned `fastembed` version) is a
migration event** — re-embed + re-baseline per the runbook in
[ADR-006](adr/006-model-provider-and-embedding-runtime.md).

### Semantic cache freshness

- `CACHE_TTL_SECONDS` (default 3600): cached answers older than the TTL are
  ignored, so price/stock changes stop being served after at most one TTL.
  `0` disables expiry.
- Ingesting/re-ingesting products invalidates the whole semantic cache
  immediately.

## Start Services

```bash
docker compose up -d --build
```

Services started:

- `api` on `:8000`
- `db` (pgvector on PostgreSQL 17)
- `phoenix` (`:6006`, OTLP on `:4317`/`:4318`)

## Health Checks

Probe endpoints:

- `GET /health/liveness`
- `GET /health/readiness`

Quick check:

```bash
curl -s http://localhost:8000/health/readiness
```

## Troubleshooting

- `api` keeps restarting: verify `TELEGRAM_WEBHOOK_SECRET` length (>=20 chars).
- readiness returns `503`: check Postgres logs and DB secret file content.
- DB auth failures from host-run commands: `DB_PASSWORD` in `.env` must equal the
  content of `./secrets/db_password.txt` (containers read the secret directly).
- slow startup: first build compiles dependencies; subsequent builds are cached.

## Branch Protection (one-time, needs repo owner)

To make a red CI actually block merges, enable branch protection on GitHub —
Settings → Branches → ruleset for `main`: require status checks **lint**,
**unit**, **eval-tier-r** + require branches up to date. Or via CLI:

```bash
gh api -X PUT repos/ThaiG2Pro/ai-agent-sale-v2/branches/main/protection \
  -f 'required_status_checks[strict]=true' \
  -f 'required_status_checks[contexts][]=lint' \
  -f 'required_status_checks[contexts][]=unit' \
  -f 'required_status_checks[contexts][]=eval-tier-r' \
  -F enforce_admins=false \
  -F 'required_pull_request_reviews=null' -F 'restrictions=null'
```

## Security Note

- Never commit `.env` or `secrets/*`.
- Containers read the DB password from the Docker secret (`db_password`); only
  host-run tooling needs it in `.env`.

## Rate Limiting (not implemented — recommendation)

- Telegram webhook endpoint should be protected by request rate limits once traffic grows.
- Start with per-chat and per-IP limits at reverse proxy layer (Nginx/Traefik) before app-level policies.
- Keep webhook ack path fast: reject excess traffic early with `429` while preserving internal worker stability.
