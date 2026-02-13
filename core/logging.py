"""Why this exists: Centralized structured logging for observability.
What it does: Configures Logfire to direct JSON logs to Stdout.
"""

import logging
import sys

import logfire

from core.config import settings


def setup_logging():
    """Configures structured logging for the application."""

    # Configure Logfire
    # In Dev (Local), we want console output.
    # logfire.configure() will handle this based on LOGFIRE_TOKEN and environment.
    logfire.configure(
        token=settings.LOGFIRE_TOKEN,
        send_to_logfire=True if settings.LOGFIRE_TOKEN else False,
    )

    # Article XII: Zero-Cost Baseline - Ensure logs go to stdout/stderr
    # Logfire already instrumented standard library logging by default if configured
    # but we'll ensure the root logger is set up.

    logging.basicConfig(
        handlers=[logfire.LogfireLoggingHandler(), logging.StreamHandler(sys.stdout)],
        level=settings.LOG_LEVEL,
        format="%(message)s",
    )

    # Article I: Modular Core - Log initial setup
    logging.info("Logging initialized with level: %s", settings.LOG_LEVEL)
