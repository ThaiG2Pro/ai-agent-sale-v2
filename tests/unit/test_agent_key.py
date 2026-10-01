"""verify_agent_key — optional X-Agent-Key guard (Spacely support groundwork)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from api import dependencies


@pytest.mark.asyncio
async def test_agent_key_open_when_unset(monkeypatch) -> None:
    monkeypatch.setattr(dependencies.settings, "AGENT_API_KEY", None)
    await dependencies.verify_agent_key(None)


@pytest.mark.asyncio
async def test_agent_key_accepts_match(monkeypatch) -> None:
    monkeypatch.setattr(dependencies.settings, "AGENT_API_KEY", "secret-123")
    await dependencies.verify_agent_key("secret-123")


@pytest.mark.asyncio
@pytest.mark.parametrize("provided", [None, "", "wrong"])
async def test_agent_key_rejects_missing_or_wrong(monkeypatch, provided) -> None:
    monkeypatch.setattr(dependencies.settings, "AGENT_API_KEY", "secret-123")
    with pytest.raises(HTTPException) as exc:
        await dependencies.verify_agent_key(provided)
    assert exc.value.status_code == 401
