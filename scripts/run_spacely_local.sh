#!/usr/bin/env bash
# Run the agent for Spacely on a SEPARATE database so the FAQ corpus never
# mixes with the shop catalog. Postgres from docker compose
# (ai-agent-postgres) is reused; only DB_NAME differs.
#
#   ./scripts/run_spacely_local.sh migrate   # create DB (if missing) + alembic upgrade head
#   ./scripts/run_spacely_local.sh ingest [URL|FILE]   # (re)load FAQ corpus
#   ./scripts/run_spacely_local.sh api       # uvicorn on :8000 (support graph: see docs/upgrade-plan-support-graph.md)
set -euo pipefail
cd "$(dirname "$0")/.."

export SUPPORT_GRAPH_ENABLED=true
export DB_NAME="${SPACELY_DB_NAME:-spacely_agent}"
export SUPPORT_CONTACT_LINK="${SPACELY_SUPPORT_LINK:-https://spacely.app/faq#lien-he}"
export EPISODIC_MEMORY_ENABLED="${EPISODIC_MEMORY_ENABLED:-true}"
export PHOENIX_PROJECT_NAME="${PHOENIX_PROJECT_NAME:-spacely-support}"

case "${1:-}" in
  migrate)
    docker exec ai-agent-postgres psql -U "${DB_USER:-user}" -d postgres -tc \
      "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 \
      || docker exec ai-agent-postgres psql -U "${DB_USER:-user}" -d postgres -c "CREATE DATABASE ${DB_NAME}"
    uv run alembic upgrade head
    ;;
  ingest)
    src="${2:-http://localhost:3000/api/v1/support/knowledge}"
    if [[ -f "$src" ]]; then
      uv run python scripts/ingest_spacely_faq.py --file "$src"
    else
      uv run python scripts/ingest_spacely_faq.py --url "$src"
    fi
    ;;
  api)
    uv run uvicorn api.main:app --host 0.0.0.0 --port "${API_PORT:-8000}"
    ;;
  *)
    echo "usage: $0 {migrate|ingest [URL|FILE]|api}" >&2; exit 2;;
esac
