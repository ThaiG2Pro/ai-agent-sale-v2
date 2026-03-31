"""Contract tests for timeout guard behavior (T069-T070)."""

from __future__ import annotations

import asyncio

import pytest

from core.tools.timeout_guard import wrap_tool_with_timeout


@pytest.mark.asyncio
async def test_tool_timeout_returns_retryable_error() -> None:
    async def _slow() -> str:
        await asyncio.sleep(0.05)
        return "ok"

    result = await wrap_tool_with_timeout(_slow(), "inventory_check", timeout_seconds=0.001)
    assert result.success is False
    assert result.is_retryable is True
    assert "timed out" in (result.error or "")


@pytest.mark.asyncio
async def test_timeout_error_includes_tool_name_and_duration() -> None:
    async def _slow() -> str:
        await asyncio.sleep(0.05)
        return "ok"

    result = await wrap_tool_with_timeout(_slow(), "order_processing", timeout_seconds=0.001)
    assert "order_processing" in (result.error or "")
    assert result.duration_ms is not None
    assert result.duration_ms >= 0
