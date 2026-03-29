"""Sales Intent Extraction Service (Week 5, FR-011).

Why this exists: Extract structured sales signals from conversation text.
What it does: Uses LiteLLM to classify intent, extract budget, urgency, timeline.
Signal gating: Skips extraction for low-value intents (FOLLOW_UP, SMALLTALK, OTHER).

Cost optimization (Article IV): 70% fewer LLM calls by skipping non-signal turns.
Graceful degradation (Article VII): Returns defaults on LiteLLM failure.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.agent.state import (
    SKIP_INTENT_EXTRACTION,
    IntentEnum,
    SalesIntentExtraction,
)
from core.config import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class SalesIntentExtractor:
    """Extract sales signals from conversation using LiteLLM (FR-011).

    Signal gating logic:
    - Skip extraction for FOLLOW_UP, SMALLTALK, OTHER (low-value turns)
    - Extract for PRICING, COMPLAINT, NEGOTIATION, ADD_ON (high-value turns)

    Cost optimization: 70% fewer LLM calls via signal gating.
    Non-blocking: Returns defaults on LiteLLM errors (Article VII).
    """

    def should_extract(self, primary_intent: IntentEnum) -> bool:
        """Check if intent warrants structured extraction (non-async, pure).

        Args:
            primary_intent: Current turn's detected intent enum value.

        Returns:
            False if intent in SKIP_INTENT_EXTRACTION, else True.
            Low-value intents (FOLLOW_UP, SMALLTALK, OTHER) skip extraction.
            High-value intents (PRICING, COMPLAINT, NEGOTIATION) trigger extraction.
        """
        return primary_intent not in SKIP_INTENT_EXTRACTION

    async def extract(self, conversation_text: str, db: AsyncSession) -> SalesIntentExtraction:
        """Extract sales signals from conversation text via LiteLLM (async).

        Calls LiteLLM with LIGHT_CHAT_MODEL to extract:
        - budget_range: Extracted budget or price constraint
        - urgency_level: LOW, MEDIUM, HIGH, or UNKNOWN (from context clues)
        - product_interest: List of mentioned products/categories
        - decision_timeline: Timeline for purchase decision
        - contact_preference: Preferred contact channel

        On LiteLLM error: Log and return defaults (urgency=UNKNOWN, others empty).
        Rationale: Intent extraction is not customer-facing; graceful degradation
        allows conversation to continue even if extraction fails.

        Args:
            conversation_text: Full conversation history to analyze.
            db: AsyncSession (reserved for future context lookups).

        Returns:
            SalesIntentExtraction model with extracted fields or safe defaults.
        """
        try:
            import litellm

            logger.debug(
                "Extracting sales intent from conversation",
                extra={"model": settings.LIGHT_CHAT_MODEL, "text_len": len(conversation_text)},
            )

            # LiteLLM call with response_format for structured output (Article XII)
            response = await litellm.acompletion(
                model=settings.LIGHT_CHAT_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a sales intent analyzer. Extract structured information "
                            "from customer conversations. Return JSON with: budget_range, "
                            "urgency_level (LOW|MEDIUM|HIGH|UNKNOWN), product_interest (list), "
                            "decision_timeline, contact_preference. If not mentioned, use null. "
                            "Do not hallucinate. Only extract what customer explicitly states."
                        ),
                    },
                    {"role": "user", "content": conversation_text},
                ],
                response_format=SalesIntentExtraction,  # Pydantic validation
                temperature=0.2,  # Low temperature for factual extraction (not creative)
                timeout=10,
            )

            # Parse structured response
            if hasattr(response, "choices") and len(response.choices) > 0:
                content = response.choices[0].message.content
                if isinstance(content, dict):
                    extraction = SalesIntentExtraction(**content)
                else:
                    # Fallback: Parse JSON string if needed
                    import json

                    extraction = SalesIntentExtraction(**json.loads(content))

                logger.debug(
                    "Intent extraction successful",
                    extra={"urgency": extraction.urgency_level.value},
                )
                return extraction

            # Response format not recognized
            logger.warning(
                "Unexpected LiteLLM response format",
                extra={"response_type": type(response)},
            )
            return SalesIntentExtraction()

        except Exception as e:
            # Graceful failure: log and return defaults (Article VII)
            logger.warning(
                "Intent extraction failed, returning defaults",
                extra={"error": str(e), "error_type": type(e).__name__},
            )
            return SalesIntentExtraction()
