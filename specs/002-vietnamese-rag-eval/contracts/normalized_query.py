"""Why this exists: Canonical type contract for the query normalization output.
What it does: Defines NormalizedQuery — the Pydantic model returned by
             AIGateway.normalize_query(). Used as response_format= in LiteLLM
             to enforce structured output without regex parsing.

FR-004 — Constitution Article VI (Schema-First, no regex parsing).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class NormalizedQuery(BaseModel):
    """Structured normalization of a raw user query.

    Produced via LiteLLM with response_format=NormalizedQuery using light-chat
    (qwen3:0.6b). No regex parsing; the model must emit valid JSON matching
    this schema.
    On LLM error, a minimal fallback is returned (canonical=raw query,
    intent=OTHER, is_valid=True to avoid false rejects).
    """

    canonical: str = Field(
        description=(
            "The cleaned, canonical form of the user query in its original language. "
            "Remove filler words but preserve meaning and language (vi/en)."
        )
    )

    detected_language: str = Field(
        description=(
            "ISO 639-1 language code. Use 'vi' for Vietnamese, 'en' for English, "
            "'mixed' for bilingual queries."
        )
    )

    intent: str = Field(
        description=(
            "Primary intent classification. Must be one of: "
            "INFO_QUERY | PRICING | COMPARISON | COMPLAINT | "
            "NEGOTIATION | AVAILABILITY | OTHER"
        )
    )

    extracted_keywords: list[str] = Field(
        description=(
            "Up to 10 meaningful keywords extracted from the query for FTS enrichment. "
            "Prefer product names, feature terms, and action words."
        )
    )

    is_valid: bool = Field(
        default=True,
        description=(
            "False if the query is spam, gibberish, or has no product-related intent. "
            "Pipeline skips retrieval and returns early when False. "
            "Heuristic pre-check (len<3 or digit-only) fires before LLM call."
        ),
    )
