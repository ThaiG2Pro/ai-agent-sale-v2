"""Unit tests for Article X recursion limit enforcement (WP3 — Feature 003).

Why this exists: the recursion limit used to be only a comment in graph.py —
LangGraph silently ran with its default of 25. make_agent_config is now the
single place every invoke site builds its RunnableConfig, so the limit is
actually enforced.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from core.agent.graph import AGENT_RECURSION_LIMIT, make_agent_config
from core.config import settings


def test_agent_recursion_limit_value():
    """Article X: 5 turns x ~4 super-steps per turn = 20 (< LangGraph default 25)."""
    assert AGENT_RECURSION_LIMIT == settings.AGENT_MAX_TURNS * 4
    assert AGENT_RECURSION_LIMIT == 20
    assert AGENT_RECURSION_LIMIT < 25  # must be tighter than LangGraph's default


def test_make_agent_config_enforces_recursion_limit():
    """Config carries recursion_limit + thread_id + db for node injection."""
    db = MagicMock()
    config = make_agent_config("session-42", db=db)

    assert config["recursion_limit"] == AGENT_RECURSION_LIMIT
    assert config["configurable"]["thread_id"] == "session-42"
    assert config["configurable"]["db"] is db


def test_make_agent_config_db_optional():
    """db defaults to None (e.g. state-read-only paths)."""
    config = make_agent_config("session-43")

    assert config["configurable"]["db"] is None
    assert config["recursion_limit"] == AGENT_RECURSION_LIMIT
