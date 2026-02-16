"""Middleware implementations for the API package.

This module holds concrete middleware classes and exception handlers.
Keep heavy-lifting out of package import-time code; importing this module
is lightweight and explicit when used by the application.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import logfire
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

if TYPE_CHECKING:
    from fastapi import HTTPException, Request


class TimingMiddleware(BaseHTTPMiddleware):
    """Middleware to measure and log request latency."""

    async def dispatch(self, request: Request, call_next):
        start_time = time.perf_counter()
        response = await call_next(request)
        process_time = time.perf_counter() - start_time

        # Article XII: Performance Goals - Health check latency monitoring
        response.headers["X-Process-Time"] = str(process_time)

        with logfire.span(
            "{method} {path} processed in {duration:.4f}s",
            method=request.method,
            path=request.url.path,
            duration=process_time,
        ):
            pass

        return response


async def global_exception_handler(request: Request, exc: Exception):
    """Global handler for uncaught exceptions."""
    # Log the full exception with Logfire
    logfire.error("Unhandled exception occurred: {error}", error=str(exc))

    return JSONResponse(
        status_code=500,
        content={"detail": "Internal Server Error", "type": exc.__class__.__name__},
    )


async def http_exception_handler(request: Request, exc: HTTPException):
    """Handler for FastAPI HTTPExceptions."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )
