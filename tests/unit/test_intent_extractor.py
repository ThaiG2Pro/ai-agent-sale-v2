"""Unit tests for SalesIntentExtractor (T048-T057).

Coverage:
- T048-T052: should_extract() gating logic (skip FOLLOW_UP, SMALLTALK, OTHER)
- T054-T057: extract() method (no signals, urgency, budget, model config)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agent.state import IntentEnum, UrgencyLevel
from services.memory.intent_extractor import SalesIntentExtractor


class TestSalesIntentExtractor:
    """Unit tests for sales intent extraction service."""

    def setup_method(self):
        """Setup for each test."""
        self.extractor = SalesIntentExtractor()

    # === Gating Logic Tests (T048-T052) ===

    def test_should_extract_follow_up_false(self):
        """T048: should_extract(FOLLOW_UP) → False (skip low-value)."""
        result = self.extractor.should_extract(IntentEnum.FOLLOW_UP)
        assert result is False

    def test_should_extract_smalltalk_false(self):
        """T049: should_extract(SMALLTALK) → False (skip low-value)."""
        result = self.extractor.should_extract(IntentEnum.SMALLTALK)
        assert result is False

    def test_should_extract_other_false(self):
        """T050: should_extract(OTHER) → False (skip low-value)."""
        result = self.extractor.should_extract(IntentEnum.OTHER)
        assert result is False

    def test_should_extract_pricing_true(self):
        """T051: should_extract(PRICING) → True (extract high-value)."""
        result = self.extractor.should_extract(IntentEnum.PRICING)
        assert result is True

    def test_should_extract_complaint_true(self):
        """T052: should_extract(COMPLAINT) → True (extract high-value)."""
        result = self.extractor.should_extract(IntentEnum.COMPLAINT)
        assert result is True

    # === Extraction Tests (T054-T057) ===

    @pytest.mark.asyncio
    async def test_extract_no_signals_returns_defaults(self):
        """T054: extract() with no signals → urgency=UNKNOWN, all fields None/empty.

        FR-013: No hallucination. If no clear signals found, return defaults.
        """
        mock_db = AsyncMock()
        conversation = "Hi, how are you?"

        with patch("litellm.acompletion") as mock_llm:
            # Mock LiteLLM response: no signals
            mock_response = MagicMock()
            mock_response.choices = [
                MagicMock(
                    message=MagicMock(
                        content={
                            "budget_range": None,
                            "urgency_level": "UNKNOWN",
                            "product_interest": [],
                            "decision_timeline": None,
                            "contact_preference": None,
                        }
                    )
                )
            ]
            mock_llm.return_value = mock_response

            result = await self.extractor.extract(conversation, mock_db)

            assert result.urgency_level == UrgencyLevel.UNKNOWN
            assert result.product_interest == []
            assert result.budget_range is None

    @pytest.mark.asyncio
    async def test_extract_urgency_detection(self):
        """T055: extract() with "cần gấp" (urgent) → urgency_level=HIGH."""
        mock_db = AsyncMock()
        conversation = "Tôi cần gấp một chiếc máy lạnh để buổi hôm nay"

        with patch("litellm.acompletion") as mock_llm:
            mock_response = MagicMock()
            mock_response.choices = [
                MagicMock(
                    message=MagicMock(
                        content={
                            "budget_range": None,
                            "urgency_level": "HIGH",
                            "product_interest": ["máy lạnh"],
                            "decision_timeline": "hôm nay",
                            "contact_preference": None,
                        }
                    )
                )
            ]
            mock_llm.return_value = mock_response

            result = await self.extractor.extract(conversation, mock_db)

            assert result.urgency_level == UrgencyLevel.HIGH
            assert "máy lạnh" in result.product_interest

    @pytest.mark.asyncio
    async def test_extract_budget_detection(self):
        """T056: extract() with "khoảng 20 triệu" → budget_range not None."""
        mock_db = AsyncMock()
        conversation = "Tôi muốn mua máy lạnh khoảng 20 triệu đồng"

        with patch("litellm.acompletion") as mock_llm:
            mock_response = MagicMock()
            mock_response.choices = [
                MagicMock(
                    message=MagicMock(
                        content={
                            "budget_range": "khoảng 20 triệu đồng",
                            "urgency_level": "UNKNOWN",
                            "product_interest": ["máy lạnh"],
                            "decision_timeline": None,
                            "contact_preference": None,
                        }
                    )
                )
            ]
            mock_llm.return_value = mock_response

            result = await self.extractor.extract(conversation, mock_db)

            assert result.budget_range is not None
            assert "20 triệu" in result.budget_range

    @pytest.mark.asyncio
    async def test_extract_uses_light_chat_model(self):
        """T057: extract() asserts model=LIGHT_CHAT_MODEL in LiteLLM call.

        Article XII: Verify cheap model is used (cost tracking).
        """
        from core.config import settings

        mock_db = AsyncMock()
        conversation = "Test conversation"

        with patch("litellm.acompletion") as mock_llm:
            mock_response = MagicMock()
            mock_response.choices = [
                MagicMock(
                    message=MagicMock(
                        content={
                            "budget_range": None,
                            "urgency_level": "UNKNOWN",
                            "product_interest": [],
                            "decision_timeline": None,
                            "contact_preference": None,
                        }
                    )
                )
            ]
            mock_llm.return_value = mock_response

            await self.extractor.extract(conversation, mock_db)

            # Verify LiteLLM called with LIGHT_CHAT_MODEL
            mock_llm.assert_called_once()
            call_kwargs = mock_llm.call_args[1]
            assert call_kwargs["model"] == settings.LIGHT_CHAT_MODEL
