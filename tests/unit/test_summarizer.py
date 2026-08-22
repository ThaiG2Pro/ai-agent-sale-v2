"""Unit tests for conversation summarization (Phase 6, T092-T107).

Tests cover:
- Summarization detection logic (should_summarize)
- LLM integration (summarize)
- Database persistence (save_summary)
- Background task orchestration (_maybe_summarize)
- Error handling and graceful degradation
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agent.state import ConversationSummaryOutput, make_initial_state
from services.memory.summarizer import ConversationSummarizer

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_db():
    """Fixture for mocking AsyncSession."""
    db = AsyncMock()
    mock_result = MagicMock()
    db.execute.return_value = mock_result
    return db


@pytest.fixture
def mock_db_factory():
    """Fixture for mocking AsyncSession factory."""

    class MockAsyncContextManager:
        def __init__(self, db):
            self.db = db

        async def __aenter__(self):
            return self.db

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    def factory():
        db = AsyncMock()
        db.commit = AsyncMock()
        return MockAsyncContextManager(db)

    return factory


class TestConversationSummarizer:
    """Test suite for conversation summarization."""

    @pytest.mark.asyncio
    async def test_should_summarize_below_threshold(self):
        """T093: message_count < threshold → False."""
        result = ConversationSummarizer.should_summarize(
            message_count=19,
            has_existing_summary=False,
            messages_since_last_summary=19,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_should_summarize_at_threshold(self):
        """T094: message_count >= threshold, no summary → True (first summary)."""
        result = ConversationSummarizer.should_summarize(
            message_count=20,
            has_existing_summary=False,
            messages_since_last_summary=20,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_should_summarize_exists_not_enough_new(self):
        """T095: summary exists, messages_since < trigger → False."""
        result = ConversationSummarizer.should_summarize(
            message_count=30,
            has_existing_summary=True,
            messages_since_last_summary=9,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_should_summarize_exists_trigger_met(self):
        """T096: summary exists, messages_since >= trigger → True (re-summarize)."""
        result = ConversationSummarizer.should_summarize(
            message_count=30,
            has_existing_summary=True,
            messages_since_last_summary=10,
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_summarize_uses_economy_model(self):
        """T098: summarize() uses LIGHT_CHAT_MODEL from settings."""
        messages = [
            {"role": "user", "content": "What's the price?"},
            {"role": "assistant", "content": "$99"},
        ]

        with patch("services.ai.AIGateway.complete", new_callable=AsyncMock) as mock_llm:
            mock_response = MagicMock()
            mock_response.choices[0].message.content = ConversationSummaryOutput(
                summary_text="Customer asked about pricing",
                products_discussed=["Product A"],
                open_questions=[],
            ).model_dump_json()
            mock_llm.return_value = mock_response

            summarizer = ConversationSummarizer()
            result = await summarizer.summarize(messages, session_id="t001")

            # Verify economy model was used
            assert result.summary_model is not None
            call_args = mock_llm.call_args
            assert call_args.kwargs.get("model") == summarizer.summary_model

    @pytest.mark.asyncio
    async def test_summarize_captures_products_discussed(self):
        """T099: summarize() with product mention → products_discussed contains it."""
        messages = [
            {"role": "user", "content": "Giá của máy lạnh bao nhiêu?"},
            {"role": "assistant", "content": "Máy lạnh của chúng tôi có giá..."},
        ]

        with patch("services.ai.AIGateway.complete", new_callable=AsyncMock) as mock_llm:
            mock_response = MagicMock()
            mock_response.choices[0].message.content = ConversationSummaryOutput(
                summary_text="Customer asked about air conditioner pricing",
                products_discussed=["máy lạnh"],
                open_questions=[],
            ).model_dump_json()
            mock_llm.return_value = mock_response

            summarizer = ConversationSummarizer()
            result = await summarizer.summarize(messages, session_id="t001")

            assert "máy lạnh" in result.products_discussed

    @pytest.mark.asyncio
    async def test_summarize_captures_open_questions(self):
        """T100: unanswered question → open_questions not empty."""
        messages = [
            {"role": "user", "content": "Giá bao nhiêu? Giao hàng bao lâu?"},
            {
                "role": "assistant",
                "content": "Giá là $99. Tôi sẽ kiểm tra vận chuyển...",
            },
        ]

        with patch("services.ai.AIGateway.complete", new_callable=AsyncMock) as mock_llm:
            mock_response = MagicMock()
            mock_response.choices[0].message.content = ConversationSummaryOutput(
                summary_text="Customer asked about pricing and shipping",
                products_discussed=[],
                open_questions=["Shipping time not resolved"],
            ).model_dump_json()
            mock_llm.return_value = mock_response

            summarizer = ConversationSummarizer()
            result = await summarizer.summarize(messages, session_id="t001")

            assert len(result.open_questions) > 0

    @pytest.mark.asyncio
    async def test_summarize_empty_messages_raises_error(self):
        """T101: empty messages list → raises ValueError."""
        summarizer = ConversationSummarizer()

        with pytest.raises(ValueError, match="empty conversation"):
            await summarizer.summarize([], session_id="t001")

    @pytest.mark.asyncio
    async def test_save_summary_stores_in_db(self, mock_db):
        """T103: save_summary() inserts row with correct fields."""
        summary = ConversationSummaryOutput(
            summary_text="Test summary",
            products_discussed=["Product A"],
            open_questions=["Q1"],
        )
        summary.summary_model = "economy-chat"

        # Mock the database execute
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        await ConversationSummarizer.save_summary(
            summary=summary,
            session_id="t001",
            customer_id="cust_001",
            turn_count=20,
            db=mock_db,
        )

        # Verify DB was called
        mock_db.execute.assert_called_once()
        mock_db.commit.assert_called_once()


# Week 5: Background Task Integration Tests (T104-T107)


@pytest.mark.asyncio
async def test_maybe_summarize_below_threshold_no_call(mock_db_factory):
    """T105: message_count < threshold → summarize() NOT called."""
    from services.memory.background import _maybe_summarize

    state = make_initial_state(
        user_message="Test",
        session_id="t001",
        customer_id="cust_001",
    )
    # Simulate 15 messages (below threshold of 20)
    state["messages"] = [{"role": "user", "content": f"msg {i}"} for i in range(15)]
    state["thread_summary_exists"] = False

    with patch.object(
        ConversationSummarizer, "summarize", new_callable=AsyncMock
    ) as mock_summarize:
        await _maybe_summarize(
            customer_id="cust_001",
            thread_id="t001",
            state=state,
            db_factory=mock_db_factory,
        )

        # Verify summarize was NOT called
        mock_summarize.assert_not_called()


@pytest.mark.asyncio
async def test_maybe_summarize_llm_error_graceful(mock_db_factory, caplog):
    """T106: LiteLLM raises error → logged, no exception propagated."""
    from services.memory.background import _maybe_summarize

    state = make_initial_state(
        user_message="Test",
        session_id="t001",
        customer_id="cust_001",
    )
    # Simulate 22 messages (above threshold)
    state["messages"] = [{"role": "user", "content": f"msg {i}"} for i in range(22)]
    state["thread_summary_exists"] = False

    with patch.object(
        ConversationSummarizer, "summarize", new_callable=AsyncMock
    ) as mock_summarize:
        mock_summarize.side_effect = ConnectionError("LLM unavailable")

        with caplog.at_level(logging.ERROR):
            # Should not raise exception (graceful degradation)
            await _maybe_summarize(
                customer_id="cust_001",
                thread_id="t001",
                state=state,
                db_factory=mock_db_factory,
            )

        # Verify error was logged
        assert any("Summarization failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_maybe_summarize_db_error_no_semantic_memory_call(mock_db_factory, caplog):
    """T107: DB INSERT fails → error logged, semantic memory NOT called."""
    from services.memory.background import _maybe_summarize

    state = make_initial_state(
        user_message="Test",
        session_id="t001",
        customer_id="cust_001",
    )
    state["messages"] = [{"role": "user", "content": f"msg {i}"} for i in range(22)]
    state["thread_summary_exists"] = False

    with patch.object(
        ConversationSummarizer, "summarize", new_callable=AsyncMock
    ) as mock_summarize:
        with patch.object(
            ConversationSummarizer, "save_summary", new_callable=AsyncMock
        ) as mock_save:
            mock_summarize.return_value = ConversationSummaryOutput(
                summary_text="Test", products_discussed=[], open_questions=[]
            )
            mock_save.side_effect = Exception("DB error")

            with caplog.at_level(logging.ERROR):
                await _maybe_summarize(
                    customer_id="cust_001",
                    thread_id="t001",
                    state=state,
                    db_factory=mock_db_factory,
                )

            # Verify error was logged
            assert any("Summarization failed" in r.message for r in caplog.records)
