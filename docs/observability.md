# Observability Guide — AI Sales Agent

## Architecture Overview

```
FastAPI / CLI / SQLAlchemy / HTTPX / LangGraph
         │
         ▼ (auto-instrumentors)
  OTel TracerProvider  ←── owned by logfire
         │
         ├─► logfire console  (structured local output, no cloud token)
         │
         └─► BatchSpanProcessor
                  │
                  ▼
         OTLPSpanExporter (gRPC)
                  │
                  ▼
    Arize Phoenix  :4317  →  UI at http://localhost:6006
```

### Active Instrumentors (wired in `core/logging.py`)

| Instrumentor | What it traces |
|---|---|
| `FastAPIInstrumentor` | HTTP requests, status codes, latency |
| `SQLAlchemyInstrumentor` | every DB query with parameters |
| `HTTPXClientInstrumentor` | **all LiteLLM outbound calls** (Ollama/OpenAI) + other HTTP |
| `LoggingInstrumentor` | injects `trace_id`/`span_id` into log records |
| `LangChainInstrumentor` | LangGraph node executions, LLM call semantics (OpenInference) |

### ⚠️ LiteLLM callback conflict — do NOT use `litellm.success_callback = ["logfire"]`

LiteLLM's `"logfire"` callback is mapped to its internal `OpenTelemetry` class, which calls
`trace.set_tracer_provider()` during initialization. Since logfire registers a `ProxyTracerProvider`
(not an `opentelemetry.sdk.trace.TracerProvider`), LiteLLM cannot reuse it and tries to override
it — triggering the `"Overriding of current TracerProvider is not allowed"` warning.

**Resolution** (`services/ai.py`): LiteLLM callbacks are NOT set. Instead:
- `HTTPXClientInstrumentor` captures all outbound HTTP calls as spans automatically
- `LangChainInstrumentor` captures LangGraph-level LLM call metadata

This gives full visibility without the TracerProvider conflict.

### Shared entry point — both API and CLI use the same setup

All processes (API server, CLI tools) call `setup_logging()` once at startup.
The instrumentors that need object references (`instrument_fastapi`, `instrument_sqlalchemy`)
are called separately only by the API. CLI processes skip those two.

```
API (api/main.py):
  setup_logging()           ← HTTPX + Logging + LangGraph instrumentors
  instrument_fastapi(app)   ← FastAPI spans
  instrument_sqlalchemy(..) ← SQLAlchemy spans

CLI (cli/rag_admin.py, cli/run_agent.py):
  setup_logging()           ← HTTPX + Logging + LangGraph instrumentors only
  # no FastAPI/SQLAlchemy needed
```

**Switching backends**: change only `_setup_logfire_with_phoenix()` in `core/logging.py`.
All CLIs and the API automatically use the new backend — no changes needed elsewhere.

---

## Default Setup: Arize Phoenix (Self-Hosted, 0 VND)

**Start Phoenix** (already in docker-compose.yml, no changes needed):

```bash
docker compose up phoenix -d
```

**Ports:**

| Port | Protocol | Purpose |
|------|----------|---------|
| 6006 | HTTP | Phoenix Web UI |
| 4317 | gRPC | OTLP traces (default) |
| 4318 | HTTP | OTLP traces (fallback) |

**Config (`.env` or `core/config.py` defaults):**

```env
OTLP_ENDPOINT=http://localhost:4317        # app on host; in compose → http://phoenix:4317 (auto)
OTEL_SERVICE_NAME=ai-sales-agent
PHOENIX_PROJECT_NAME=ai-sales-agent        # Phoenix UI project traces land in
```

> **App on host vs in Docker:** when the API runs via `docker compose up api`, compose
> overrides `OTLP_ENDPOINT` to `http://phoenix:4317` (the in-network service address) —
> `localhost:4317` only works for a host-run app. Nothing to configure manually.

> **Persistence:** Phoenix stores traces in the `phoenix_data` volume
> (`PHOENIX_WORKING_DIR=/mnt/data`) — they survive container restarts.
> `docker compose down -v` deletes them.

**Verify traces are flowing:**

1. Start the app: `uv run uvicorn api.main:app --reload`
2. Make any request: `curl http://localhost:8000/health`
3. Open Phoenix UI: `http://localhost:6006`
4. Open the `ai-sales-agent` project (name = `PHOENIX_PROJECT_NAME`) → Traces

**Find a customer conversation (Sessions view):**

Every agent invocation (API `/agent/query` + `/agent/stream`, Telegram, CLI) is wrapped in
`openinference.instrumentation.using_attributes(session_id=…, user_id=…)`, so all
LangGraph/LLM spans carry `session.id` and `user.id`:

1. Phoenix UI → project `ai-sales-agent` → **Sessions** tab: one row per `session_id`,
   with full multi-turn conversation history.
2. Or filter traces: `attributes.session.id == 'telegram_12345'` /
   `attributes.user.id == '<customer_id>'`.

**Per-node spans (WP-V3-2):**

Every LangGraph node execution emits a `node.<name>` span (kind `CHAIN`) as a child of the
turn's trace — so one `/agent/query` shows a parent-child tree with per-node latency:
`node.router_node → node.retrieval_node → node.confidence_node → node.answer_node → …`.

- Attributes: `session.id`, `user.id` (same OpenInference names as the parent trace, so the
  session filters above also match node spans), plus `node.name`, `intent`, `model_used`,
  `declined`. Raw message content is never attached (R-SEC-002).
- A node exception is recorded on its span (status `ERROR` + exception event) — failed turns
  show exactly which node broke.
- Kill-switch: `OTEL_NODE_SPANS_ENABLED=false` disables the wrapper entirely (zero overhead).
  It is read at **graph build time** — restart the app after changing it.

---

## Switching Backends

> The only file to edit is `core/logging.py :: _setup_logfire_with_phoenix()`.
> All instrumentors and the rest of the codebase stay unchanged.

### Option A — Logfire Cloud (production monitoring)

**When to use:** Staging/prod — deep Python monitoring, alerts, dashboards.

**Install:**

```bash
uv add "logfire[fastapi]"  # already installed
```

**Changes to `core/logging.py`:**

```python
def _setup_logfire_with_phoenix() -> None:
    import logfire

    # Remove the OTLPSpanExporter block.
    # Set LOGFIRE_TOKEN env var (from https://logfire.pydantic.dev).
    logfire.configure(
        service_name=settings.OTEL_SERVICE_NAME,
        token=settings.LOGFIRE_TOKEN,  # set in .env
        send_to_logfire=True,          # ← enable cloud
        inspect_arguments=False,
        distributed_tracing=True,      # suppress propagated-context warning
        console=logfire.ConsoleOptions(verbose=False),
        # No additional_span_processors needed — logfire handles export
    )
```

**Config:**

```env
LOGFIRE_TOKEN=your-token-from-logfire-dashboard
```

---

### Option B — LangSmith (LangGraph-specific traces)

**When to use:** Need deep LangGraph trace inspection, run comparison, prompt playground.

**Install:**

```bash
uv add langsmith
```

**Changes to `core/logging.py`:**

```python
def _setup_logfire_with_phoenix() -> None:
    import logfire
    logfire.configure(
        token=None, send_to_logfire=False,
        console=logfire.ConsoleOptions(verbose=False),
    )
    # LangSmith is activated via env vars — no code change needed

def _register_instrumentors() -> None:
    # ... existing instrumentors ...
    # Remove LangChainInstrumentor() — LangSmith has its own callback system
```

**Config:**

```env
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your-langsmith-api-key
LANGCHAIN_PROJECT=ai-sales-agent
```

> LangSmith auto-activates via env vars. Phoenix will still receive
> FastAPI/SQLAlchemy/HTTPX spans; LangGraph spans go to LangSmith instead.

---

### Option C — Grafana + Jaeger (open-source, self-hosted)

**When to use:** Team already runs Grafana stack; want unified infra.

**Docker Compose addition:**

```yaml
services:
  jaeger:
    image: jaegertracing/all-in-one:latest
    ports:
      - "16686:16686"   # Jaeger UI
      - "14250:14250"   # gRPC collector
      - "4317:4317"     # OTLP gRPC (replaces Phoenix port)
    restart: always

  prometheus:
    image: prom/prometheus:latest
    ports:
      - "9090:9090"
    restart: always

  grafana:
    image: grafana/grafana:latest
    ports:
      - "3000:3000"
    restart: always
```

**Changes to `core/logging.py`** (only the endpoint changes):

```python
def _setup_logfire_with_phoenix() -> None:
    import logfire
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    jaeger_exporter = OTLPSpanExporter(
        endpoint="http://localhost:4317",  # Jaeger OTLP gRPC
        insecure=True,
    )

    logfire.configure(
        service_name=settings.OTEL_SERVICE_NAME,
        token=None,
        send_to_logfire=False,
        console=logfire.ConsoleOptions(verbose=False),
        additional_span_processors=[BatchSpanProcessor(jaeger_exporter)],
    )
```

**Config:**

```env
OTLP_ENDPOINT=http://localhost:4317  # same value, now points to Jaeger
```

> After switching, stop the `phoenix` docker service and start `jaeger` instead.
> View traces at `http://localhost:16686`.

---

## Dual Export (Phoenix + LangSmith simultaneously)

```python
def _setup_logfire_with_phoenix() -> None:
    import logfire
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    processors = [BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.OTLP_ENDPOINT, insecure=True))]

    logfire.configure(
        service_name=settings.OTEL_SERVICE_NAME,
        token=None,
        send_to_logfire=False,
        console=logfire.ConsoleOptions(verbose=False),
        additional_span_processors=processors,
    )
    # LangSmith still activates via LANGCHAIN_TRACING_V2=true env var
```

---

## Troubleshooting

### Traces not appearing in Phoenix

1. Verify Phoenix is running: `curl http://localhost:6006/healthz`
2. Verify OTLP port is open: `nc -zv localhost 4317`
3. Check `OTLP_ENDPOINT` in config matches docker-compose port mapping
4. Look for `[OTEL]` errors in app startup logs

### "Overriding of current TracerProvider is not allowed" warning

**Cause:** Something called `trace.set_tracer_provider()` before `logfire.configure()`.

**Fix:** Ensure `setup_logging()` in `core/logging.py` is the **first** thing called at
app startup, before any other imports that might initialize OTel.

### gRPC vs HTTP OTLP

If gRPC (port 4317) is blocked by a firewall or proxy, switch to HTTP:

```python
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # http
exporter = OTLPSpanExporter(endpoint="http://localhost:4318/v1/traces")
```

Update config: `OTLP_ENDPOINT=http://localhost:4318`

---

## Quick Reference

| Environment | Backend | Command |
|---|---|---|
| Dev (local) | Phoenix | `docker compose up phoenix -d` |
| Staging | Logfire Cloud | set `LOGFIRE_TOKEN` in env |
| Prod (budget) | Phoenix | keep phoenix in docker-compose |
| Prod (deep LangGraph) | LangSmith | set `LANGCHAIN_API_KEY` in env |
| Self-hosted infra | Grafana+Jaeger | swap phoenix → jaeger in compose |
