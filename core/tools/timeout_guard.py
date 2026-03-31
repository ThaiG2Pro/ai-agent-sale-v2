"""Timeout guards for async tool execution."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from core.config import settings
from core.tools.models import ToolResult

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Awaitable


async def call_with_timeout(
    coro: Awaitable[Any],
    timeout_seconds: float,
    operation_name: str = "operation",
    fallback_value: Any = None,
    raise_on_timeout: bool = True,
) -> Any:
    """Execute coroutine with timeout and optional fallback value."""
    start = time.perf_counter()
    try:
        async with asyncio.timeout(timeout_seconds):
            return await coro
    except TimeoutError:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.warning(
            "Tool operation timed out",
            extra={
                "operation_name": operation_name,
                "timeout_seconds": timeout_seconds,
                "duration_ms": duration_ms,
            },
        )
        if raise_on_timeout:
            raise
        return fallback_value


def get_tool_timeout(tool_name: str) -> float:
    """Resolve per-tool timeout from settings with default fallback."""
    key = f"TOOL_TIMEOUT_{tool_name.upper()}"
    value = getattr(settings, key, None)
    return float(value if value is not None else settings.TOOL_TIMEOUT_DEFAULT)


async def wrap_tool_with_timeout(
    tool_coro: Awaitable[Any],
    tool_name: str,
    timeout_seconds: float | None = None,
) -> ToolResult:
    """Wrap tool call with timeout and map result to ToolResult."""
    timeout = timeout_seconds if timeout_seconds is not None else get_tool_timeout(tool_name)
    start = time.perf_counter()
    try:
        data = await call_with_timeout(
            tool_coro,
            timeout_seconds=timeout,
            operation_name=tool_name,
            raise_on_timeout=True,
        )
        return ToolResult(
            success=True,
            data=data,
            error=None,
            is_retryable=False,
            tool_name=tool_name,
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    except TimeoutError:
        return ToolResult(
            success=False,
            data=None,
            error=f"{tool_name} timed out after {timeout:.1f}s",
            is_retryable=True,
            tool_name=tool_name,
            duration_ms=(time.perf_counter() - start) * 1000,
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            data=None,
            error=str(exc),
            is_retryable=False,
            tool_name=tool_name,
            duration_ms=(time.perf_counter() - start) * 1000,
        )
