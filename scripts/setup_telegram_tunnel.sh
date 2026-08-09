#!/usr/bin/env bash
# ==============================================================================
# Telegram Tunnel & Webhook Auto Setup Script
# Automatically starts ngrok tunnel, updates .env, and registers Telegram webhook.
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

echo "=== Telegram Tunnel & Webhook Setup ==="

# 1. Check .env file
if [ ! -f ".env" ]; then
    echo "❌ Error: .env file not found in project root!"
    exit 1
fi

# Load variables from .env
BOT_TOKEN=$(grep -E '^TELEGRAM_BOT_TOKEN=' .env | cut -d '=' -f2- | tr -d '\r"')
WEBHOOK_SECRET=$(grep -E '^TELEGRAM_WEBHOOK_SECRET=' .env | cut -d '=' -f2- | tr -d '\r"')
API_PORT=$(grep -E '^API_PORT=' .env | cut -d '=' -f2- | tr -d '\r"')
API_PORT="${API_PORT:-8000}"

if [ -z "$BOT_TOKEN" ] || [ "$BOT_TOKEN" = "your_bot_token_from_botfather" ]; then
    echo "❌ Error: TELEGRAM_BOT_TOKEN is not configured in .env"
    exit 1
fi

if [ -z "$WEBHOOK_SECRET" ]; then
    echo "❌ Error: TELEGRAM_WEBHOOK_SECRET is not configured in .env"
    exit 1
fi

# 2. Check for tunnel tool (ngrok or cloudflared)
TUNNEL_TOOL=""
if command -v ngrok >/dev/null 2>&1; then
    TUNNEL_TOOL="ngrok"
elif command -v cloudflared >/dev/null 2>&1; then
    TUNNEL_TOOL="cloudflared"
else
    echo "❌ Error: Neither ngrok nor cloudflared is installed!"
    echo "Please install ngrok or cloudflared to create an HTTPS tunnel."
    exit 1
fi

echo "🔹 Tunnel tool detected: $TUNNEL_TOOL"

PUBLIC_URL=""

if [ "$TUNNEL_TOOL" = "ngrok" ]; then
    # Verify if ngrok local API is responding
    if ! curl -s http://127.0.0.1:4040/api/tunnels >/dev/null 2>&1; then
        echo "🚀 Starting ngrok tunnel on port $API_PORT..."
        pkill -f ngrok || true
        nohup ngrok http "$API_PORT" >/tmp/ngrok.log 2>&1 &
        sleep 4
    else
        echo "ℹ️  ngrok is already running."
    fi

    # Fetch public HTTPS URL from ngrok local API
    PUBLIC_URL=$(python3 -c "
import json, urllib.request
try:
    with urllib.request.urlopen('http://127.0.0.1:4040/api/tunnels') as res:
        data = json.loads(res.read().decode())
        for t in data.get('tunnels', []):
            if t.get('proto') == 'https':
                print(t.get('public_url'))
                break
except Exception as e:
    pass
")
elif [ "$TUNNEL_TOOL" = "cloudflared" ]; then
    if ! pgrep -f "cloudflared tunnel" >/dev/null 2>&1; then
        echo "🚀 Starting cloudflared tunnel on port $API_PORT..."
        nohup cloudflared tunnel --url "http://localhost:$API_PORT" >/tmp/cloudflared.log 2>&1 &
        sleep 4
    else
        echo "ℹ️  cloudflared is already running."
    fi

    PUBLIC_URL=$(grep -oE 'https://[-a-zA-Z0-9.]+\.trycloudflare\.com' /tmp/cloudflared.log | tail -n 1)
fi

if [ -z "$PUBLIC_URL" ]; then
    echo "❌ Error: Failed to retrieve public HTTPS URL from $TUNNEL_TOOL."
    echo "Check logs in /tmp/ngrok.log or /tmp/cloudflared.log"
    exit 1
fi

FULL_WEBHOOK_URL="${PUBLIC_URL%/}/webhooks/telegram"

echo "✅ Public HTTPS Tunnel URL: $PUBLIC_URL"
echo "✅ Full Telegram Webhook URL: $FULL_WEBHOOK_URL"

# 3. Update .env TELEGRAM_WEBHOOK_URL
if grep -q "^TELEGRAM_WEBHOOK_URL=" .env; then
    sed -i "s|^TELEGRAM_WEBHOOK_URL=.*|TELEGRAM_WEBHOOK_URL=${FULL_WEBHOOK_URL}|" .env
else
    echo "TELEGRAM_WEBHOOK_URL=${FULL_WEBHOOK_URL}" >> .env
fi
echo "📝 Updated TELEGRAM_WEBHOOK_URL in .env"

# 4. Register Webhook with Telegram API
echo "🔄 Registering webhook with Telegram API..."
RESPONSE=$(curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"${FULL_WEBHOOK_URL}\",\"secret_token\":\"${WEBHOOK_SECRET}\"}")

echo "Telegram Response: $RESPONSE"

# 5. Verify Webhook Info
echo "🔍 Verifying Telegram Webhook status..."
INFO_RESPONSE=$(curl -s "https://api.telegram.org/bot${BOT_TOKEN}/getWebhookInfo")
echo "Webhook Info: $INFO_RESPONSE"

# 6. Restart API container if running in Docker
if docker ps | grep -q "ai-agent-api"; then
    echo "🔄 Restarting ai-agent-api container to pick up updated .env..."
    docker compose restart api >/dev/null 2>&1 || true
    echo "✅ API container restarted."
fi

echo "=================================================================="
echo "🎉 Setup complete! You can now send messages to your Telegram bot."
echo "=================================================================="
