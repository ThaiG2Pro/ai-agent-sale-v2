"""Unit tests for SalesIntentExtractor service.

Tests cover:
- Intent classification (should_extract logic)
- Field extraction (budget, urgency, products, timeline, contact preference)
- Signal gating (skip FOLLOW_UP/OTHER/SMALLTALK)
- Graceful failure (LLM errors, malformed responses)
"""


class TestSalesIntentExtractor:
    """Test suite for sales intent extraction service."""

    pass
