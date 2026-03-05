#!/bin/bash
# Start API server and agent CLI guide

set -e

echo "=========================================="
echo "Week 3 Agent System — Startup Guide"
echo "=========================================="
echo ""
echo "MODE 1: Direct Agent Invocation (No API)"
echo "  $ uv run python -m cli.run_agent \"Toi muon hoi macbook pro con hang khong?\""
echo ""
echo "MODE 2: API Server + CLI (with HTTP)"
echo "  Terminal 1:"
echo "    $ uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload"
echo ""
echo "  Terminal 2:"
echo "    $ uv run python -m cli.run_agent \"Toi muon hoi macbook pro con hang khong?\" --api"
echo ""
echo "MODE 3: Streaming (Direct)"
echo "  $ uv run python -m cli.run_agent \"Toi muon hoi macbook pro con hang khong?\" --stream"
echo ""
echo "MODE 4: Streaming (via API)"
echo "  $ uv run python -m cli.run_agent \"Toi muon hoi macbook pro con hang khong?\" --stream --api"
echo ""
echo "API Endpoints:"
echo "  POST   http://localhost:8000/agent/query"
echo "  POST   http://localhost:8000/agent/stream"
echo "  GET    http://localhost:8000/docs (Swagger UI)"
echo ""
echo "Starting API server on port 8000..."
echo "Press Ctrl+C to stop."
echo "=========================================="
echo ""

uv run uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
