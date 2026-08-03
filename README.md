# AI Sales Agent (SME-Ready)

This is a high-performance, asynchronous AI Sales Agent designed for SMEs, following a "Zero-Cost-First" and "Offline-First" philosophy.

## Telegram Bot Setup

The project supports Telegram webhook integration via `POST /webhooks/telegram`.

1. Create a bot using `@BotFather` and copy `TELEGRAM_BOT_TOKEN`.
2. Set `TELEGRAM_WEBHOOK_SECRET` (minimum 20 chars) and `TELEGRAM_WEBHOOK_URL` in `.env`.
3. Start services with Docker:

```bash
docker compose up -d --build
```

4. Configure webhook:

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"${TELEGRAM_WEBHOOK_URL}\",\"secret_token\":\"${TELEGRAM_WEBHOOK_SECRET}\"}"
```

For full deployment instructions, see `docs/deployment.md`.

## Hardware Requirements

To run the system locally with AI capabilities (via Ollama), the following minimum hardware is required:

- **CPU**: ≥ 4 cores
- **RAM**: ≥ 8 GB
- **VRAM (GPU)**: ≥ 4 GB (Recommended for smooth local model performance)

## Development Setup

1. **Install uv**: `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. **Setup environment**: `uv sync`
3. **Start Infrastructure**: `docker-compose up -d`
4. **Local AI**: Ensure [Ollama](https://ollama.com/) is installed and running.

## Linting

- Run project lint checks and environment verification:

```bash
./scripts/lint.sh check
```

- Auto-format the code with `ruff`:

```bash
./scripts/lint.sh fix
```

- Install as a local Git pre-commit hook (copies the script to `.git/hooks/pre-commit`):

```bash
./scripts/lint.sh install-hook
```

Notes:
- These commands use `uv` (the project's task runner). Ensure you have the environment set up and `uv` installed.
- The `check` action runs `ruff check`, `ruff format --check` and `uv sync --dry-run` to validate environment rules for `uv sync`.

## CI (GitHub Actions)

`.github/workflows/ci.yml` runs on every push/PR to `main`, three sequential jobs
ordered by cost (fail fast, cheap first):

1. **lint** — `ruff check` + `ruff format --check`.
2. **unit** — `pytest -m "not integration and not performance"` against a real
   Postgres 17 + pgvector service container (LLM calls are fully mocked).
3. **eval-tier-r** — seeds the demo catalog and runs the retrieval recall gate
   (`./scripts/eval_gate.sh --tier r`) with in-process fastembed embeddings.
   **No LLM API key is needed anywhere in CI** (Zero-Cost-First).

Not in CI (run manually): `pytest -m integration` (needs a chat LLM),
Tier-F eval (needs Groq key; nightly workflow planned in WP-V3-3), and
`pytest -m performance` benchmarks (flaky on weak runners).

### Branch protection (one-time, needs repo owner)

To make a red CI actually block merges, enable branch protection on GitHub —
Settings → Branches → Add branch ruleset for `main`:

- ✅ Require status checks to pass before merging — select **lint**, **unit**,
  **eval-tier-r**.
- ✅ Require branches to be up to date before merging.

Or via CLI:

```bash
gh api -X PUT repos/ThaiG2Pro/ai-agent-sale-v2/branches/main/protection \
  -f 'required_status_checks[strict]=true' \
  -f 'required_status_checks[contexts][]=lint' \
  -f 'required_status_checks[contexts][]=unit' \
  -f 'required_status_checks[contexts][]=eval-tier-r' \
  -F enforce_admins=false \
  -F 'required_pull_request_reviews=null' -F 'restrictions=null'
```
