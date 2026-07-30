#!/usr/bin/env bash
# Why this exists: one-command entry for the WP-V2-0 tiered eval gate.
# What it does: forwards all args to scripts/eval_gate.py under uv.
#   ./scripts/eval_gate.sh                 # Tier-R (retrieval-only, no chat LLM)
#   ./scripts/eval_gate.sh --tier f        # Tier-F smoke (needs chat LLM)
#   ./scripts/eval_gate.sh --tier all      # full pre-release run (manual)
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python scripts/eval_gate.py "$@"
