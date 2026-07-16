"""Centralized observability setup for AI Sales Agent.

Architecture (SME Pro 2026 Constitution — Minimalist-First):
  Layer 1 — Python stdlib logging: stdout/JSON fallback, always works.
  Layer 2 — logfire owns the OTel TracerProvider (console output + spans).
             Phoenix receives spans via additional OTLP gRPC processor.
  Layer 3 — Auto-instrumentors: FastAPI, SQLAlchemy, HTTPX, stdlib logging,
             LangChain/LangGraph (OpenInference).

Default backend: self-hosted Arize Phoenix (docker-compose service).
  UI:        http://localhost:6006
  OTLP gRPC: localhost:4317  (set OTLP_ENDPOINT in config/env)
  OTLP HTTP: localhost:4318  (fallback if gRPC blocked)

See docs/observability.md for backend-switching guide.

DESIGN NOTE — single TracerProvider pattern:
  logfire.configure() must be called FIRST because it registers the global OTel
  TracerProvider. Phoenix export is attached as an additional_span_processors arg,
  not via a second trace.set_tracer_provider() call (which would raise a warning).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime

from core.config import settings

_initialized = False


def setup_logging() -> None:
    """Initialize full observability stack: logfire console + OTEL → Phoenix.

    Idempotent — safe to call multiple times.
    Call ONCE at process start, before FastAPI app creation.
    """
    global _initialized
    if _initialized:
        return

    _setup_python_logging()
    _setup_logfire_with_phoenix()
    _register_instrumentors()

    logging.getLogger(__name__).info(
        "Observability ready — traces → Phoenix http://localhost:6006"
    )
    _initialized = True


# ---------------------------------------------------------------------------
# PII masking helpers (FR-008 + steering/security.md — no bare PII in logs)
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")
# Vietnamese phone formats: 0xxxxxxxxx (10 digits) or +84/84 prefix (9-10 more digits).
_PHONE_RE = re.compile(r"(?<![\d\w])(?:\+?84|0)(?:[ .-]?\d){8,10}(?![\d\w])")

# Log-record keys treated as customer identity → masked in JSON output.
_IDENTITY_KEYS = frozenset({"customer_id", "chat_id", "session_id", "thread_id", "email", "phone"})
# Log-record keys that must never reach stdout with a value.
_SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "authorization", "admin_key")


def mask_email(email: str) -> str:
    """`nguyen.van.a@example.com` → `ng***@example.com`."""
    local, _, domain = str(email).partition("@")
    if not domain:
        return "***"
    return f"{local[:2]}***@{domain}"


def mask_phone(phone: str) -> str:
    """Keep the last 3 digits only: `0912345678` → `*******678`."""
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) < 4:
        return "***"
    return "*" * (len(digits) - 3) + digits[-3:]


def mask_identifier(value: object) -> str:
    """Mask an opaque identity value (customer_id, chat_id, session_id...)."""
    s = str(value)
    if "@" in s:
        return mask_email(s)
    if len(s) <= 4:
        return "***"
    return f"{s[:3]}***{s[-2:]}"


def mask_pii(text: str) -> str:
    """Scrub emails and Vietnamese phone numbers from free-form text."""
    text = _EMAIL_RE.sub(lambda m: mask_email(m.group()), text)
    return _PHONE_RE.sub(lambda m: mask_phone(m.group()), text)


# ---------------------------------------------------------------------------
# Layer 1: Python stdlib logging — JSON structured output (FR-008)
# ---------------------------------------------------------------------------

# Attributes present on every LogRecord — anything else came in via `extra=`.
_BUILTIN_RECORD_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JsonFormatter(logging.Formatter):
    """Structured JSON log lines for stdout (FR-008).

    Core fields: timestamp, level, logger, message (+ request_id / OTel
    trace_id/span_id when present). `extra=` fields are appended with
    identity keys masked and secret-like keys redacted.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": mask_pii(record.getMessage()),
        }
        # Injected by OTel LoggingInstrumentor / request middleware when present.
        for source_attr, field in (
            ("request_id", "request_id"),
            ("otelTraceID", "trace_id"),
            ("otelSpanID", "span_id"),
        ):
            value = getattr(record, source_attr, None)
            if value:
                payload[field] = value

        for key, value in record.__dict__.items():
            if key in _BUILTIN_RECORD_ATTRS or key.startswith("_") or key in payload:
                continue
            if any(marker in key.lower() for marker in _SECRET_KEY_MARKERS):
                payload[key] = "[REDACTED]"
            elif key in _IDENTITY_KEYS:
                payload[key] = mask_identifier(value)
            elif isinstance(value, str):
                payload[key] = mask_pii(value)
            else:
                payload[key] = value

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def _setup_python_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=settings.LOG_LEVEL, handlers=[handler], force=True)


# ---------------------------------------------------------------------------
# Layer 2: logfire (TracerProvider owner) + Phoenix OTLP export
# ---------------------------------------------------------------------------


def _setup_logfire_with_phoenix() -> None:
    """Configure logfire as the global OTel TracerProvider.

    Spans flow to two destinations simultaneously:
      a) logfire console — structured local dev output (no cloud token)
      b) Phoenix OTLP   — persistent traces at http://localhost:6006

    Why logfire owns the provider (not phoenix.otel.register()):
      OTel only allows ONE global TracerProvider. logfire.configure() must win
      because it installs its own context propagators and sampling logic.
      Phoenix is attached as an additional span processor instead.

    Switching backends → docs/observability.md.
    """
    import logfire
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    phoenix_exporter = OTLPSpanExporter(
        endpoint=settings.OTLP_ENDPOINT,  # default: http://localhost:4317
        insecure=True,  # no TLS needed for local Phoenix
    )

    logfire.configure(
        service_name=settings.OTEL_SERVICE_NAME,
        token=None,
        send_to_logfire=False,
        # Disable f-string introspection — only works in interactive shells,
        # produces noisy warnings in production / scripted contexts.
        inspect_arguments=False,
        # Silence "Found propagated trace context" warning — we intentionally
        # propagate context from CLI → API → Phoenix for end-to-end traces.
        distributed_tracing=True,
        console=logfire.ConsoleOptions(verbose=False),
        additional_span_processors=[BatchSpanProcessor(phoenix_exporter)],
    )


# ---------------------------------------------------------------------------
# Instrumentors — auto-instrument HTTPX, logging, LangGraph
# ---------------------------------------------------------------------------


def _register_instrumentors() -> None:
    """Activate import-time auto-instrumentors.

    FastAPI and SQLAlchemy instrumentors need app/engine objects — they are
    called separately via instrument_fastapi() / instrument_sqlalchemy() below.
    """
    # stdlib logging → injects trace_id/span_id into every log record
    from opentelemetry.instrumentation.logging import LoggingInstrumentor

    LoggingInstrumentor().instrument(set_logging_format=True)

    # HTTPX → spans for every outbound HTTP call (LiteLLM → Ollama/OpenAI/etc.)
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument()

    # LangChain/LangGraph → OpenInference spans for each agent node
    # Produces spans visible in Phoenix's "LLM Traces" panel
    from openinference.instrumentation.langchain import LangChainInstrumentor

    LangChainInstrumentor().instrument()


# ---------------------------------------------------------------------------
# Per-object instrumentors (called after object creation)
# ---------------------------------------------------------------------------


def instrument_fastapi(app) -> None:
    """Instrument a FastAPI app — call once after app = FastAPI(...).

    Usage (api/main.py):
        from core.logging import instrument_fastapi
        instrument_fastapi(app)
    """
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)


def instrument_sqlalchemy(engine) -> None:
    """Instrument a SQLAlchemy engine — call once after engine is created.

    Usage (api/main.py lifespan):
        from core.logging import instrument_sqlalchemy
        instrument_sqlalchemy(engine.sync_engine)
    """
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    SQLAlchemyInstrumentor().instrument(engine=engine)
