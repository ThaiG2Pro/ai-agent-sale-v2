# Docker Deployment Guide

## Prerequisites

- Docker Engine + Docker Compose plugin
- A DB password file at `./secrets/db_password.txt`
- `.env` file copied from `.env.example`

## Environment Variables

Use `.env.example` as baseline, then set:

- `DB_USER`, `DB_NAME`, `DB_PORT`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `TELEGRAM_WEBHOOK_URL`
- `DATABASE_POOL_SIZE=20`
- `DATABASE_MAX_OVERFLOW=0`

Optional explicit DSN format:

- `DATABASE_URL=postgresql+asyncpg://<user>:<password>@<host>:<port>/<db>`

> `TELEGRAM_WEBHOOK_SECRET` has **no default** in `docker-compose.yml` — compose
> refuses to start until you set it (env or `.env`, min 20 chars).

## LLM Provider Options

Model settings (`LIGHT_CHAT_MODEL`, `CHAT_MODEL`, `POWERFUL_CHAT_MODEL`,
`EMBED_MODEL`) accept **any LiteLLM model string**. LiteLLM reads provider API
keys straight from the environment; `docker-compose.yml` passes them through
(`GEMINI_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, `ANTHROPIC_API_KEY`).

### Option A — Cloud chat + local embeddings (recommended for fast demos)

No Ollama needed for chat; keeps embeddings compatible with the existing DB:

```bash
# .env
CHAT_MODEL=gemini/gemini-2.5-flash
LIGHT_CHAT_MODEL=gemini/gemini-2.5-flash
POWERFUL_CHAT_MODEL=gemini/gemini-2.5-pro
GEMINI_API_KEY=<your key>
# EMBED_MODEL stays ollama/bge-m3 (local)
```

### Option B — Fully local (Ollama on the host)

The `api` container reaches host Ollama via `host.docker.internal`
(`extra_hosts: host-gateway` is preconfigured); the default
`OLLAMA_BASE_URL=http://host.docker.internal:11434` works out of the box.

### ⚠️ Embedding dimension constraint

pgvector columns are `Vector(1024)` (bge-m3). Only switch `EMBED_MODEL` to a
model that can emit **1024-dim vectors** (e.g. OpenAI `text-embedding-3-large`
with `dimensions=1024`). Otherwise keep `EMBED_MODEL=ollama/bge-m3` and switch
chat models only. A mismatched dimension fails fast at the AI gateway with
"Configuration Error: Model Mismatch".

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
- DB auth failures: ensure `./secrets/db_password.txt` matches DB credentials.
- slow startup: first build compiles dependencies; subsequent builds are cached.

## Security Note

- Never commit `.env` or `secrets/*`.
- Use Docker secrets (`db_password`) instead of plaintext env passwords.

## Rate Limiting (Week 7 Consideration)

- Telegram webhook endpoint should be protected by request rate limits once traffic grows.
- Start with per-chat and per-IP limits at reverse proxy layer (Nginx/Traefik) before app-level policies.
- Keep webhook ack path fast: reject excess traffic early with `429` while preserving internal worker stability.
