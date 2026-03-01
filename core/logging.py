"""Why this exists: Centralized structured logging and observability.
What it does: Configures logfire for console output (local dev; no cloud auth).
          Phoenix traces will come from Python logging integration if needed.
"""

from __future__ import annotations

import logging
import os

from core.config import settings

_initialized = False


def setup_logging():
    """Configures logfire for local development (console output, no cloud).

    Why: Logfire cloud auth (401 errors) is not needed for local dev.
    Use logfire for structured logging, skip OTLP export entirely.
    Phoenix can be monitored separately via direct OTEL integration if needed.
    """
    global _initialized
    if _initialized:
        return

    # Disable logfire cloud and OTLP export
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "false"
    os.environ["LOGFIRE_TOKEN"] = ""
    # Disable OpenTelemetry SDK to prevent OTLP export attempts
    os.environ["OTEL_SDK_DISABLED"] = "true"

    # Now import and configure logfire
    import logfire

    # Configure logfire for console-only (no cloud, no OTLP)
    logfire.configure(
        token=None,
        send_to_logfire=False,
        console=logfire.ConsoleOptions(verbose=True),
    )

    # Configure Python logging
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    logging.info("Observability initialized (logfire console, Cloud/OTLP: disabled)")
    _initialized = True
