"""Unit tests for v3-0 P3 (T09) resilience: fallback ladder, 429 handling,
timeout/turn budget, token-budget gate, backpressure primitives.

Eval gate #2 of the locked proposal: zero 429/degradation tests existed —
these mock the provider (never hit Groq for real).
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.config import settings
from services import resilience


@pytest.fixture(autouse=True)
def _reset():
    resilience.reset_for_tests()
    yield
    resilience.reset_for_tests()


def _llm_response(content: str = "ok"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
    )


class _RateLimit(Exception):
    def __str__(self) -> str:  # mimic litellm RateLimitError text
        return "RateLimitError: 429 Too Many Requests"


MESSAGES = [{"role": "user", "content": "hi"}]


# ---------- error classification ----------


def test_classify_429():
    assert resilience._classify_error(_RateLimit()) == "rate_limit"


def test_classify_auth():
    assert resilience._classify_error(Exception("401 authentication failed")) == "auth"


def test_classify_transient():
    assert resilience._classify_error(Exception("connection reset")) == "transient"


# ---------- ladder behavior ----------


@pytest.mark.asyncio
async def test_ladder_top_rung_success_not_degraded():
    with patch(
        "services.ai.AIGateway.complete", new=AsyncMock(return_value=_llm_response())
    ) as comp:
        res = await resilience.complete_with_ladder(MESSAGES, intent="INFO_QUERY")
    assert res.degraded is False
    assert res.model_used == "premium-chat"
    assert comp.await_count == 1


@pytest.mark.asyncio
async def test_ladder_429_jumps_rung_and_cools_down():
    """429 on premium → NO retry on that rung, cooldown set, 8b answers."""

    async def side_effect(*args, **kwargs):
        if kwargs["model"] == "premium-chat":
            raise _RateLimit()
        return _llm_response("from 8b")

    with patch("services.ai.AIGateway.complete", new=AsyncMock(side_effect=side_effect)) as comp:
        res = await resilience.complete_with_ladder(MESSAGES, intent="INFO_QUERY")
    assert res.degraded is True
    assert res.model_used == "fallback-chat-8b"
    # premium called exactly once (429 not retried at ladder level)
    premium_calls = [c for c in comp.await_args_list if c.kwargs["model"] == "premium-chat"]
    assert len(premium_calls) == 1
    # rung is cooling down now
    assert not resilience._rung_available("premium-chat")


@pytest.mark.asyncio
async def test_ladder_cooldown_skips_premium_on_next_turn():
    resilience._cool_down("premium-chat", 600)
    with patch(
        "services.ai.AIGateway.complete", new=AsyncMock(return_value=_llm_response())
    ) as comp:
        res = await resilience.complete_with_ladder(MESSAGES, intent="INFO_QUERY")
    assert res.model_used == "fallback-chat-8b"
    assert comp.await_args_list[0].kwargs["model"] == "fallback-chat-8b"


@pytest.mark.asyncio
async def test_risky_intent_never_gets_local_rung():
    """ORDER: all cloud rungs down → degraded holding, local NEVER tried."""
    with patch("services.ai.AIGateway.complete", new=AsyncMock(side_effect=_RateLimit())) as comp:
        res = await resilience.complete_with_ladder(MESSAGES, intent="ORDER_PLACEMENT")
    assert res.degraded is True
    assert res.response is None
    tried_models = [c.kwargs["model"] for c in comp.await_args_list]
    assert "economy-chat" not in tried_models
    assert resilience.degraded_turn_count() == 1


@pytest.mark.asyncio
async def test_non_risky_intent_falls_to_local():
    async def side_effect(*args, **kwargs):
        if kwargs["model"] == "economy-chat":
            return _llm_response("local")
        raise _RateLimit()

    with patch("services.ai.AIGateway.complete", new=AsyncMock(side_effect=side_effect)):
        res = await resilience.complete_with_ladder(MESSAGES, intent="INFO_QUERY")
    assert res.model_used == "economy-chat"
    assert res.degraded is True


@pytest.mark.asyncio
async def test_turn_budget_deadline_skips_all_rungs():
    """3.2: expired ~30s turn budget → degraded without any provider call."""
    with patch("services.ai.AIGateway.complete", new=AsyncMock()) as comp:
        res = await resilience.complete_with_ladder(
            MESSAGES, intent="INFO_QUERY", deadline=time.monotonic() - 1
        )
    assert res.degraded is True
    assert res.response is None
    comp.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_error_moves_to_next_rung():
    async def side_effect(*args, **kwargs):
        if kwargs["model"] == "premium-chat":
            raise TimeoutError()
        return _llm_response("8b")

    with patch("services.ai.AIGateway.complete", new=AsyncMock(side_effect=side_effect)):
        res = await resilience.complete_with_ladder(MESSAGES, intent="INFO_QUERY")
    assert res.model_used == "fallback-chat-8b"
    # a timeout is transient — the rung is NOT cooled down
    assert resilience._rung_available("premium-chat")


@pytest.mark.asyncio
async def test_token_budget_gate_skips_premium():
    """3.3: at ~90% of the daily premium cap the ladder degrades early."""
    with (
        patch.object(resilience, "premium_budget_exhausted", new=AsyncMock(return_value=True)),
        patch(
            "services.ai.AIGateway.complete", new=AsyncMock(return_value=_llm_response())
        ) as comp,
    ):
        res = await resilience.complete_with_ladder(MESSAGES, intent="INFO_QUERY", db=object())
    assert res.model_used == "fallback-chat-8b"
    assert comp.await_args_list[0].kwargs["model"] == "fallback-chat-8b"


@pytest.mark.asyncio
async def test_preferred_model_starts_ladder_lower():
    """Escalation chose economy → the ladder must not silently upgrade."""
    with patch(
        "services.ai.AIGateway.complete", new=AsyncMock(return_value=_llm_response())
    ) as comp:
        res = await resilience.complete_with_ladder(
            MESSAGES, intent="INFO_QUERY", preferred_model="economy-chat"
        )
    assert res.model_used == "economy-chat"
    assert res.degraded is False  # its own top rung
    assert comp.await_count == 1


# ---------- counters / helpers ----------


def test_degraded_counters_and_alert_drain():
    resilience.record_degraded_turn()
    resilience.record_degraded_turn()
    assert resilience.degraded_turn_count() == 2
    assert resilience.drain_degraded_since_alert() == 2
    assert resilience.drain_degraded_since_alert() == 0
    assert resilience.degraded_turn_count() == 2  # total not drained


def test_holding_message_contains_minutes():
    assert f"~{settings.HITL_WAIT_ALERT_MINUTES} phút" in resilience.holding_message()


@pytest.mark.asyncio
async def test_turn_semaphore_capacity():
    sem = resilience.turn_semaphore()
    assert sem._value == settings.LLM_MAX_CONCURRENT_TURNS
