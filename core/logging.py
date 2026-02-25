"""Why this exists: Centralized structured logging and observability.
What it does: Configures OpenTelemetry (OTLP) with fallback to JSON Stdout.
"""

from __future__ import annotations

import logging

from core.config import settings

_initialized = False


def setup_logging():
    """Configures OpenTelemetry and structured logging.

    Why: Logfire manages its own OTel initialization.
    We let logfire handle TracerProvider setup to avoid conflicts.
    """
    global _initialized
    if _initialized:
        return

    # Import logfire early to avoid duplicate provider initialization
    import logfire

    # Configure Logfire (which manages OpenTelemetry internally)
    logfire.configure(
        token=settings.LOGFIRE_TOKEN,
        send_to_logfire=bool(settings.LOGFIRE_TOKEN),
        distributed_tracing=True,
        console=logfire.ConsoleOptions(verbose=True)
        if not settings.LOGFIRE_TOKEN
        else False,
    )

    # Bridge Python logging to logfire (single handler path, no duplicates)
    logging.basicConfig(
        handlers=[logfire.LogfireLoggingHandler()],
        level=settings.LOG_LEVEL,
        format="%(message)s",
    )

    logging.info(
        "Observability initialized (OTLP: %s, Cloud: %s)",
        settings.OTLP_ENDPOINT,
        "Enabled" if settings.LOGFIRE_TOKEN else "Disabled",
    )
    _initialized = True
