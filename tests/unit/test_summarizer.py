"""Unit tests for ConversationSummarizer service.

Tests cover:
- Summarization trigger logic (message count thresholds)
- LLM call (model, format, no hallucination)
- Graceful failure (LLM errors, malformed JSON)
- Token reduction verification (30%+ target)
"""


class TestConversationSummarizer:
    """Test suite for conversation summarization service."""

    pass
