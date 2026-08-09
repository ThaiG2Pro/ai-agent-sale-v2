#!/usr/bin/env bash
# ==============================================================================
# Script khởi chạy llama-server tối ưu ROCm HIP cho AMD Radeon 780M
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

LLAMA_SERVER_BIN="$PROJECT_ROOT/bin/llama-server"
MODEL_PATH="${1:-$HOME/.ollama/models/blobs/sha256-1741e5b2d062b07acf048bf0d2c514dadf2a48f94e2b4aa0cfe069af3838ee2f}"
PORT="${PORT:-8080}"

if [ ! -f "$LLAMA_SERVER_BIN" ]; then
    echo "❌ Error: llama-server binary not found at $LLAMA_SERVER_BIN!"
    exit 1
fi

if [ ! -f "$MODEL_PATH" ]; then
    echo "❌ Error: Model file not found at $MODEL_PATH!"
    exit 1
fi

echo "🚀 Khởi chạy llama-server ROCm HIP trên port $PORT..."
echo "📦 Model: $MODEL_PATH"

exec "$LLAMA_SERVER_BIN" \
    -m "$MODEL_PATH" \
    --host 0.0.0.0 \
    --port "$PORT" \
    -ngl 99 \
    -fa on \
    -ctk q8_0 -ctv q8_0 \
    -t 8 \
    -c 8192 \
    --alias economy-chat,light-chat,hosted_vllm/economy-chat,hosted_vllm/light-chat,ollama/economy-chat,openai/economy-chat
