"""Unit tests for timeout guard utilities (T076, T083)."""

from __future__ import annotations

import asyncio

import pytest

from core.tools.timeout_guard import call_with_timeout, get_tool_timeout


@pytest.mark.asyncio
async def test_call_with_timeout_raises_on_timeout() -> None:
    async def _slow() -> str:
        await asyncio.sleep(0.05)
        return "ok"

    with pytest.raises(TimeoutError):
        await call_with_timeout(_slow(), timeout_seconds=0.001, operation_name="slow_op")


@pytest.mark.asyncio
async def test_call_with_timeout_returns_fallback() -> None:
    async def _slow() -> str:
        await asyncio.sleep(0.05)
        return "ok"

    result = await call_with_timeout(
        _slow(),
        timeout_seconds=0.001,
        operation_name="slow_op",
        fallback_value="fallback",
        raise_on_timeout=False,
    )
    assert result == "fallback"


def test_get_tool_timeout_uses_specific_and_default(monkeypatch) -> None:
    from core.tools import timeout_guard as tg

    monkeypatch.setattr(tg.settings, "TOOL_TIMEOUT_INVENTORY_CHECK", 7)
    monkeypatch.setattr(tg.settings, "TOOL_TIMEOUT_DEFAULT", 5)

    assert get_tool_timeout("inventory_check") == 7.0
    assert get_tool_timeout("unknown_tool") == 5.0
