"""Unit tests for customer_support_node (Phase 13, T040/T041).

Covers: empathetic message via LLM, fallback message when the LLM fails,
SupportQueue insert, HITLMetadata status update when a pause exists, and
resilience when persistence fails.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langgraph.types import Command

from core.agent.nodes.customer_support import customer_support_node
from core.config import settings


def _llm_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.choices[0].message.content = text
    return resp


@pytest.fixture
def mock_db():
    return AsyncMock()


@pytest.fixture
def mock_config(mock_db):
    return {"configurable": {"db": mock_db}}


@pytest.fixture
def state():
    msg = MagicMock()
    msg.type = "human"
    msg.content = "I want a refund"
    return {
        "session_id": "s1",
        "hitl_rejection_reason": "suspicious request",
        "messages": [msg],
    }


@pytest.mark.asyncio
async def test_support_uses_llm_message_and_queues_escalation(state, mock_config, mock_db):
    with patch(
        "services.ai.AIGateway.complete",
        AsyncMock(return_value=_llm_response("We're sorry — a human will help you.")),
    ):
        result = await customer_support_node(state, mock_config)

    assert isinstance(result, Command)
    assert result.goto == "answer_node"
    # O27/Case 11: the concrete rejection reason is appended when the LLM
    # message omits it — the customer must always see WHY.
    assert result.update["response"].startswith("We're sorry — a human will help you.")
    assert "suspicious request" in result.update["response"]
    assert result.update["hitl_triggered"] is False
    # SupportQueue insert executed + flushed
    mock_db.execute.assert_awaited()
    mock_db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_support_falls_back_when_llm_fails(state, mock_config):
    with patch(
        "services.ai.AIGateway.complete",
        AsyncMock(side_effect=RuntimeError("model offline")),
    ):
        result = await customer_support_node(state, mock_config)

    # Offline-First: fallback message still carries reason + support link
    assert "suspicious request" in result.update["response"]
    assert settings.SUPPORT_CONTACT_LINK in result.update["response"]


@pytest.mark.asyncio
async def test_support_updates_hitl_metadata_when_paused(state, mock_config, mock_db, monkeypatch):
    # Kill-switch off → no handoff-package queries; the original two executes only.
    monkeypatch.setattr("core.config.settings.ORDER_HITL_V3_ENABLED", False)
    state["hitl_pause_id"] = "pause-1"
    with patch(
        "services.ai.AIGateway.complete",
        AsyncMock(return_value=_llm_response("ok")),
    ):
        await customer_support_node(state, mock_config)

    # Two executes: SupportQueue insert + HITLMetadata status update
    assert mock_db.execute.await_count == 2


@pytest.mark.asyncio
async def test_support_survives_persistence_failure(state, mock_config, mock_db):
    """DB failure must not crash the turn — the customer still gets a response."""
    mock_db.execute.side_effect = RuntimeError("db down")
    with patch(
        "services.ai.AIGateway.complete",
        AsyncMock(return_value=_llm_response("ok")),
    ):
        result = await customer_support_node(state, mock_config)

    assert result.goto == "answer_node"
    assert result.update["response"].startswith("ok")
