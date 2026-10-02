# Telegram Bot Setup Guide

## Create Bot with BotFather

1. Open Telegram and find `@BotFather`.
2. Send `/newbot`.
3. Choose bot display name and username (must end with `bot`).
4. Save the generated token.

## Configure Environment

Set these values in `.env`:

- `TELEGRAM_BOT_TOKEN=<token from BotFather>`
- `TELEGRAM_WEBHOOK_SECRET=<random string, min 20 chars>`
- `TELEGRAM_WEBHOOK_URL=https://<your-domain>/webhooks/telegram`

## Register Webhook

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"${TELEGRAM_WEBHOOK_URL}\",\"secret_token\":\"${TELEGRAM_WEBHOOK_SECRET}\"}"
```

## Verify Webhook

```bash
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```

Check that:

- `url` matches your webhook URL
- `last_error_message` is empty
- `pending_update_count` remains low

## Local Testing (tunnel)

Telegram needs a public HTTPS URL. For a laptop, `scripts/setup_telegram_tunnel.sh`
starts an ngrok tunnel, writes `TELEGRAM_WEBHOOK_URL` into `.env`, and registers
the webhook in one go:

```bash
./scripts/setup_telegram_tunnel.sh
```
