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
