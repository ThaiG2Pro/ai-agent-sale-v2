"""Why this exists: Centralized structured logging and observability.
What it does: Configures OpenTelemetry (OTLP) to export traces to local Phoenix.
"""

from __future__ import annotations

import logging
import os

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import set_tracer_provider

from core.config import settings

_initialized = False


def setup_logging():
    """Configures OpenTelemetry to export traces to local Phoenix (no cloud auth).

    Why: Direct OTLP/HTTP exporter sends to Phoenix @ localhost:6006/v1/traces.
    Avoids logfire cloud authentication 401 errors; uses raw OpenTelemetry instead.
    """
    global _initialized
    if _initialized:
        return

    # Set OTEL environment variables for OTLP HTTP exporter
    # Phoenix supports HTTP endpoint at :6006/v1/traces
    otlp_http_endpoint = settings.OTLP_ENDPOINT.replace(":4317", ":6006")
    os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = otlp_http_endpoint
    os.environ["OTEL_EXPORTER_OTLP_PROTOCOL"] = "http/protobuf"
    os.environ["OTEL_SDK_DISABLED"] = "false"

    # Create OTLP exporter (Phoenix HTTP endpoint)
    otlp_exporter = OTLPSpanExporter(
        endpoint=f"{otlp_http_endpoint}/v1/traces",
    )

    # Create resource (service name + version)
    resource = Resource.create(
        {
            "service.name": settings.OTEL_SERVICE_NAME,
            "service.version": "1.0.0",
        }
    )

    # Create and set global tracer provider
    trace_provider = TracerProvider(resource=resource)
    trace_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    set_tracer_provider(trace_provider)

    # Configure Python logging to use OpenTelemetry handler
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logging.info(
        "Observability initialized (OTLP HTTP: %s/v1/traces, Cloud: Disabled)",
        otlp_http_endpoint,
    )
    _initialized = True
