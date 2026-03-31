# Quickstart: Telegram Integration & Production Docker

**Branch**: `006-telegram-docker` | **Date**: 2026-03-30  
**Purpose**: Quick deployment guide for Telegram bot + Docker production setup

---

## Prerequisites

- Docker 24+ and Docker Compose v2 installed
- Telegram account (to create a bot)
- Domain with HTTPS (for production webhook) OR ngrok (for local testing)
- PostgreSQL 17 with pgvector (included in docker-compose.yml)

---

## Step 1: Create Telegram Bot

### 1.1 Talk to BotFather

1. Open Telegram and search for `@BotFather`
2. Send `/newbot` command
3. Follow prompts:
   - **Bot name**: `Your Sales Agent` (display name)
   - **Username**: `your_sales_agent_bot` (must end in `bot`)
4. **Save the bot token**: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`

### 1.2 Configure Bot Settings

```bash
# In BotFather chat:
/setdescription @your_sales_agent_bot
# Enter: "AI-powered sales assistant for SMEs"

/setcommands @your_sales_agent_bot
start - Start conversation
help - Get help
```

---

## Step 2: Configure Environment Variables

### 2.1 Copy Example Environment File

```bash
cp .env.example .env
```

### 2.2 Edit `.env` File

```bash
# Telegram Configuration
TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_WEBHOOK_SECRET=$(openssl rand -hex 32)  # Generate random secret
TELEGRAM_WEBHOOK_URL=https://your-domain.com/webhooks/telegram

# Database Configuration (Docker defaults)
DATABASE_URL=postgresql+asyncpg://ai_agent:secure_password@db:5432/ai_agent
DATABASE_POOL_SIZE=20
DATABASE_MAX_OVERFLOW=0

# Tool Timeout Configuration
TOOL_TIMEOUT_DEFAULT=5.0
TOOL_TIMEOUT_INVENTORY_CHECK=5.0
TOOL_TIMEOUT_ORDER_PROCESSING=10.0

# Application Configuration
LOG_LEVEL=info
PYTHONUNBUFFERED=1
```

### 2.3 Secure Secrets (Production)

```bash
# Create secrets directory (excluded from Git)
mkdir -p secrets/

# Store sensitive credentials
echo "secure_password" > secrets/db_password.txt
chmod 600 secrets/db_password.txt
```

---

## Step 3: Local Development Setup

### 3.1 Start Services with Docker Compose

```bash
# Build and start all services
docker-compose up --build

# Or in detached mode (background)
docker-compose up -d --build
```

**Services Started**:
- `api`: FastAPI application (port 8000)
- `db`: PostgreSQL 17 with pgvector (port 5432)
- `phoenix`: Arize Phoenix observability (ports 6006, 4317, 4318)

### 3.2 Verify Services are Healthy

```bash
# Check container status
docker-compose ps

# Test health check endpoints
curl http://localhost:8000/health/liveness
# Expected: {"status":"alive","timestamp":...}

curl http://localhost:8000/health/readiness
# Expected: {"status":"ready","checks":{...}}
```

### 3.3 Run Database Migrations

```bash
# Apply Alembic migrations (inside container)
docker-compose exec api alembic upgrade head

# Or run from host (if uv installed locally)
uv run alembic upgrade head
```

### 3.4 Set Up Webhook (Local Testing)

**Option A: ngrok (Recommended for Local Testing)**

```bash
# Install ngrok: https://ngrok.com/download
# Start tunnel
ngrok http 8000

# Copy the HTTPS URL (e.g., https://abc123.ngrok.io)
# Update .env:
TELEGRAM_WEBHOOK_URL=https://abc123.ngrok.io/webhooks/telegram

# Restart app to load new env
docker-compose restart api
```

**Option B: Set Webhook via API**

```bash
# Set webhook with Telegram Bot API
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d "{
    \"url\": \"${TELEGRAM_WEBHOOK_URL}\",
    \"secret_token\": \"${TELEGRAM_WEBHOOK_SECRET}\",
    \"max_connections\": 10,
    \"allowed_updates\": [\"message\", \"callback_query\"]
  }"

# Verify webhook is set
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
```

---

## Step 4: Test the Bot

### 4.1 Send Test Message

1. Open Telegram
2. Search for `@your_sales_agent_bot`
3. Send `/start` or "What products do you have?"
4. Verify bot responds within 3 seconds

### 4.2 Monitor Logs

```bash
# Follow application logs
docker-compose logs -f api

# Expected output:
# INFO: Received Telegram update 123456789
# INFO: Processing message from chat_id=1234567890
# INFO: Hybrid search completed in 234ms
# INFO: Sent response to Telegram chat_id=1234567890
```

### 4.3 Test Timeout & Retry UI

```bash
# Simulate slow tool (for testing timeout)
# In api/tools/inventory.py, add:
# await asyncio.sleep(10)  # Force timeout

# Send message that triggers inventory check
# Expected: Bot responds with "Inventory check timed out" + Retry button
```

---

## Step 5: Production Deployment

### 5.1 Server Requirements

- **CPU**: 2+ cores (recommended 4 for async workload)
- **RAM**: 2GB minimum, 4GB recommended
- **Disk**: 20GB (for Docker images + database)
- **OS**: Ubuntu 22.04+ or Debian 12+

### 5.2 Set Up Reverse Proxy (Nginx)

```nginx
# /etc/nginx/sites-available/sales-agent
upstream fastapi {
    server localhost:8000;
}

server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate /etc/letsencrypt/live/your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/your-domain.com/privkey.pem;

    location /webhooks/telegram {
        proxy_pass http://fastapi;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Telegram-Bot-Api-Secret-Token $http_x_telegram_bot_api_secret_token;
        
        # Webhook timeout (Telegram expects response within 60s)
        proxy_read_timeout 60s;
        proxy_connect_timeout 10s;
        proxy_send_timeout 60s;
    }

    location /health {
        proxy_pass http://fastapi;
        access_log off;  # Don't log health checks
    }
}
```

### 5.3 Deploy with Docker Compose

```bash
# Clone repository
git clone https://github.com/your-org/ai-agent-sale-v2.git
cd ai-agent-sale-v2

# Checkout feature branch
git checkout 006-telegram-docker

# Configure production environment
cp .env.example .env
nano .env  # Edit with production values

# Build and start in production mode
docker-compose -f docker-compose.yml up -d --build

# Verify deployment
docker-compose ps
curl https://your-domain.com/health/readiness
```

### 5.4 Set Production Webhook

```bash
# Set webhook to production URL
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d "{
    \"url\": \"https://your-domain.com/webhooks/telegram\",
    \"secret_token\": \"${TELEGRAM_WEBHOOK_SECRET}\",
    \"max_connections\": 40,
    \"allowed_updates\": [\"message\", \"callback_query\"]
  }"

# Verify webhook status
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
# Check: pending_update_count should be 0
```

---

## Step 6: Monitoring & Maintenance

### 6.1 Monitor Logs

```bash
# Stream logs from all services
docker-compose logs -f

# View only API logs
docker-compose logs -f api

# Filter for errors
docker-compose logs api | grep ERROR
```

### 6.2 Check Metrics

```bash
# Arize Phoenix UI (observability)
# Open: http://localhost:6006

# Database query stats
docker-compose exec db psql -U ai_agent -c "
  SELECT count(*) as total_updates,
         count(DISTINCT chat_id) as unique_users,
         max(created_at) as last_update
  FROM telegram_updates;
"

# Connection pool status (from health check)
curl -s http://localhost:8000/health/readiness | jq '.checks.connection_pool'
```

### 6.3 Database Cleanup (Weekly Cron)

```bash
# Add to crontab: crontab -e
# Clean up old telegram_updates (keep 7 days)
0 2 * * 0 docker-compose exec -T db psql -U ai_agent -c "DELETE FROM telegram_updates WHERE created_at < NOW() - INTERVAL '7 days';"
```

### 6.4 Container Resource Limits

Edit `docker-compose.yml` to add resource constraints:

```yaml
services:
  api:
    # ... existing config ...
    deploy:
      resources:
        limits:
          cpus: '2.0'
          memory: 2G
        reservations:
          cpus: '0.5'
          memory: 512M
```

---

## Step 7: Troubleshooting

### 7.1 Webhook Not Receiving Updates

**Symptom**: Bot doesn't respond to messages

**Checks**:
```bash
# 1. Verify webhook is set
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getWebhookInfo"
# Check: url, has_custom_certificate, pending_update_count

# 2. Test webhook endpoint directly
curl -X POST http://localhost:8000/webhooks/telegram \
  -H "Content-Type: application/json" \
  -H "X-Telegram-Bot-Api-Secret-Token: ${TELEGRAM_WEBHOOK_SECRET}" \
  -d '{"update_id":1,"message":{"message_id":1,"chat":{"id":123},"date":1711766400,"text":"test"}}'

# 3. Check firewall/nginx logs
sudo tail -f /var/log/nginx/error.log

# 4. Verify HTTPS certificate is valid
curl -I https://your-domain.com/webhooks/telegram
```

**Solutions**:
- Ensure webhook URL is HTTPS (Telegram requirement)
- Check `X-Telegram-Bot-Api-Secret-Token` header matches `.env`
- Verify ngrok/reverse proxy is forwarding requests
- Check application logs: `docker-compose logs api`

---

### 7.2 Database Connection Errors

**Symptom**: Health check fails, "cannot connect to database"

**Checks**:
```bash
# 1. Verify PostgreSQL is running
docker-compose ps db

# 2. Test connection from host
docker-compose exec db pg_isready -U ai_agent

# 3. Check connection string
docker-compose exec api env | grep DATABASE_URL

# 4. Test from app container
docker-compose exec api python -c "
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
engine = create_engine('${DATABASE_URL}', poolclass=NullPool)
conn = engine.connect()
print('Connected!')
"
```

**Solutions**:
- Ensure `depends_on` in docker-compose.yml includes `db`
- Check database logs: `docker-compose logs db`
- Verify password matches in `.env` and `docker-compose.yml`
- Wait for database init: `docker-compose exec db pg_isready`

---

### 7.3 Tool Timeout Errors

**Symptom**: Bot responds with "Tool timed out. Please try again."

**Checks**:
```bash
# Check timeout configuration
docker-compose exec api env | grep TOOL_TIMEOUT

# Review tool execution logs
docker-compose logs api | grep "tool_timeout"

# Test tool directly (if CLI available)
docker-compose exec api python -c "
import asyncio
from core.tools.inventory import check_inventory
asyncio.run(check_inventory('product_123'))
"
```

**Solutions**:
- Increase timeout for specific tool: `TOOL_TIMEOUT_INVENTORY_CHECK=10.0`
- Check external service availability (if tool calls API)
- Review tool implementation for blocking calls
- Add caching to reduce tool latency

---

### 7.4 Docker Image Size Too Large

**Symptom**: Image exceeds 300MB target

**Diagnosis**:
```bash
# Check actual image size
docker images ai-agent:latest

# Analyze layer sizes
docker history ai-agent:latest --human

# Find large files
docker run --rm ai-agent:latest du -sh /home/appuser/.local/lib/python3.13/site-packages/* | sort -h | tail -20
```

**Solutions**:
- Ensure multi-stage build is used
- Verify `--no-dev` flag in uv sync (excludes test deps)
- Remove unnecessary system packages from runtime stage
- Check for accidentally copied files (.git, __pycache__, etc.)

---

## Step 8: Performance Tuning

### 8.1 Connection Pool Optimization

```python
# core/database.py - Adjust based on load
engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,          # Max concurrent connections
    max_overflow=0,        # No additional connections beyond pool_size
    pool_pre_ping=True,    # Verify connection before use
    pool_recycle=3600,     # Recycle connections after 1 hour
)
```

### 8.2 Uvicorn Worker Tuning

```dockerfile
# Dockerfile - Adjust CMD based on CPU cores
CMD ["uvicorn", "api.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \          # Set to number of CPU cores
     "--loop", "uvloop", \
     "--http", "httptools"]
```

### 8.3 Docker Health Check Tuning

```yaml
# docker-compose.yml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health/liveness"]
  interval: 30s      # How often to check
  timeout: 10s       # Max time for check to complete
  retries: 3         # Failures before marking unhealthy
  start_period: 30s  # Grace period during startup
```

---

## Quick Reference

### Common Commands

```bash
# Start services
docker-compose up -d

# Stop services
docker-compose down

# Restart API only
docker-compose restart api

# View logs
docker-compose logs -f api

# Execute command in container
docker-compose exec api bash

# Run migrations
docker-compose exec api alembic upgrade head

# Python shell
docker-compose exec api python

# Database shell
docker-compose exec db psql -U ai_agent

# Rebuild after code changes
docker-compose up -d --build api

# Clean up everything (including volumes)
docker-compose down -v
```

### Environment Variables Quick Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | - | Bot token from BotFather (required) |
| `TELEGRAM_WEBHOOK_SECRET` | - | Secret for webhook verification (required) |
| `TELEGRAM_WEBHOOK_URL` | - | Public HTTPS URL for webhook (required) |
| `TOOL_TIMEOUT_DEFAULT` | 5.0 | Default timeout for tools (seconds) |
| `DATABASE_URL` | - | PostgreSQL connection string (required) |
| `DATABASE_POOL_SIZE` | 20 | Max database connections |
| `LOG_LEVEL` | info | Logging level (debug/info/warning/error) |

---

## Next Steps

After completing this setup:

1. **Test thoroughly**: Send various message types, test timeout scenarios
2. **Monitor metrics**: Use Arize Phoenix to track performance
3. **Optimize prompts**: Tune agent responses based on user feedback
4. **Scale if needed**: Add more workers, increase connection pool
5. **Implement Week 7**: Add semantic cache, rate limiting, advanced monitoring

---

**Questions or Issues?**
- Check logs: `docker-compose logs -f api`
- Review contracts: `specs/006-telegram-docker/contracts/`
- Read data model: `specs/006-telegram-docker/data-model.md`

---

**Status**: Phase 1 complete. Ready for task generation (`/speckit.tasks`).
