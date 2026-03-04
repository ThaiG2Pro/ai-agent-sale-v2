"""Why this exists: Centralized gateway for all AI model interactions.
What it does: Provides async wrappers for LiteLLM with fallback and latency tracking.
             Week 2 addition: NormalizedQuery schema + normalize_query() for
             query rewriting.
"""

from __future__ import annotations

import time
from typing import Any, Literal

import logfire
from litellm import Router
from pydantic import BaseModel, Field

from core.ai_config import LITELLM_CONFIG

# ── Query normalisation schema (FR-004) ───────────────────────────────────────


class NormalizedQuery(BaseModel):
    """
    Why this exists: Structured representation of a cleaned user query (FR-004).
    No regex; produced via LiteLLM response_format (Pydantic model).
    """

    canonical: str = Field(
        description=("The cleaned, canonical form of the user query in its original language.")
    )
    detected_language: str = Field(description="Detected language code, e.g. 'vi' or 'en'.")
    intent: str = Field(
        description=(
            "Primary intent: INFO_QUERY | PRICING | COMPARISON | "
            "COMPLAINT | NEGOTIATION | AVAILABILITY | OTHER"
        )
    )
    extracted_keywords: list[str] = Field(
        description=("Up to 10 meaningful keywords extracted from the query for FTS enrichment.")
    )
    is_valid: bool = Field(
        default=True,
        description=(
            "False if the query is spam, gibberish, or has no product-related intent. "
            "Pipeline skips retrieval when False."
        ),
    )


# ── Metadata enrichment schemas (Phase 1 - Ingestion) ──────────────────────────


class KeywordExtraction(BaseModel):
    """
    Why this exists: Structured keyword extraction for products (Phase 1).
    What it does: Extracts relevant keywords via LiteLLM response_format.
    """

    keywords: list[str] = Field(
        ...,
        min_length=3,
        max_length=10,
        description="3-10 keywords for hybrid search (FTS + vector)",
    )
    rationale: str = Field(description="Brief explanation of why these keywords were chosen")


class ProductMetadata(BaseModel):
    """
    Why this exists: Structured metadata for products (Phase 1 ingestion).
    What it does: Enriches products with specs, category, intent, keywords, summary.
    Enforces structure via Pydantic validation.
    """

    product_id: str = Field(..., description="SKU or unique product identifier")
    technical_specs: dict[str, Any] = Field(
        default_factory=dict,
        description="Technical specifications: voltage, frequency, thermal_limit, etc.",
    )
    keywords: list[str] = Field(
        ...,
        min_length=3,
        max_length=10,
        description="High-quality keywords for hybrid search",
    )
    seo_summary: str = Field(
        ...,
        max_length=100,
        description="SEO-friendly summary under 100 characters for display",
    )
    category: str = Field(
        ...,
        max_length=50,
        description="Product category for filtering/metadata signals",
    )
    intent: Literal["commercial", "consumer"] = Field(
        ...,
        description="Sales intent: commercial (B2B) or consumer (B2C)",
    )

    @classmethod
    def minimal(cls, product_id: str, name: str) -> ProductMetadata:
        """Fallback: minimal metadata when enrichment fails."""
        return cls(
            product_id=product_id,
            technical_specs={},
            keywords=[product_id.lower(), name.lower(), "product"],
            seo_summary=name[:50],
            category="unknown",
            intent="consumer",
        )


# Initialize LiteLLM Router for advanced routing and fallbacks
ai_router = Router(**LITELLM_CONFIG)

# LiteLLM traces are captured via HTTPXClientInstrumentor (registered in core/logging.py).
# Do NOT set litellm.success_callback = ["logfire"] — LiteLLM's own OTel integration
# tries to create a new TracerProvider, conflicting with logfire's ProxyTracerProvider
# (they are different types and OTel only allows one provider).
# HTTPX auto-instrumentation produces spans for every outbound call to Ollama/OpenAI.


class AIGateway:
    """
    Why this exists: Unified interface for AI operations (Chat & Embedding).
    Article V: Asynchronous I/O Mandate.
    Article X: Model Selection & Cost Efficiency.
    """

    @staticmethod
    async def complete(
        messages: list[dict[str, str]],
        model: str = "economy-chat",
        stream: bool = False,
        **kwargs,
    ) -> Any:
        """
        Why this exists: Generates text responses with automatic fallback.
        What it does: Wraps litellm.acompletion with latency monitoring.
        FR-016: Model switching latency < 2s.
        """
        start_time = time.perf_counter()

        try:
            logfire.info("AI Completion started: {model}", model=model)
            response = await ai_router.acompletion(
                model=model, messages=messages, stream=stream, **kwargs
            )

            latency = time.perf_counter() - start_time
            logfire.info(
                "AI Completion finished: {model}, Latency: {latency:.4f}s",
                model=model,
                latency=latency,
            )

            return response

        except Exception as e:
            latency = time.perf_counter() - start_time
            logfire.error(
                "AI Completion failed: {model}, Latency: {latency:.4f}s, Error: {err}",
                model=model,
                latency=latency,
                err=str(e),
            )

            # FR-015: Manual fallback if router fallback fails
            if model == "economy-chat":
                logfire.warn("Falling back to premium-chat due to error")
                return await AIGateway.complete(
                    messages=messages, model="premium-chat", stream=stream, **kwargs
                )
            raise e

    @staticmethod
    async def embed(
        input_text: str | list[str], model: str = "economy-embedding", **kwargs
    ) -> list[list[float]]:
        """
        Why this exists: Generates text embeddings for RAG and caching.
        What it does: Wraps litellm.aembedding with latency monitoring.
        """
        start_time = time.perf_counter()

        # Ensure input is a list for consistent processing
        if isinstance(input_text, str):
            input_text = [input_text]

        try:
            logfire.info("AI Embedding started: {model}", model=model)
            response = await ai_router.aembedding(model=model, input=input_text, **kwargs)

            latency = time.perf_counter() - start_time
            logfire.info(
                "AI Embedding finished: {model}, Latency: {latency:.4f}s",
                model=model,
                latency=latency,
            )

            # Extract embeddings from response
            from core.config import settings

            expected_dim = settings.EMBED_DIMENSION
            embeddings = [data["embedding"] for data in response.data]
            for emb in embeddings:
                if len(emb) != expected_dim:
                    logfire.error(
                        (
                            "Configuration Error: Model Mismatch — "
                            "embedding dimension {got} (expected {exp})"
                        ),
                        got=len(emb),
                        exp=expected_dim,
                        model=model,
                    )
                    raise ValueError(
                        f"Configuration Error: Model Mismatch — "
                        f"embedding dimension {len(emb)} (expected {expected_dim})"
                    )
            return embeddings

        except Exception as e:
            latency = time.perf_counter() - start_time
            logfire.error(
                "AI Embedding failed: {model}, Latency: {latency:.4f}s, Error: {err}",
                model=model,
                latency=latency,
                err=str(e),
            )
            raise e

    @staticmethod
    async def normalize_query(query: str) -> NormalizedQuery:
        """
        Why this exists: Cleans and structures Vietnamese/English queries before
        retrieval (FR-004).
        What it does: Uses LiteLLM response_format=NormalizedQuery to produce
        structured output. No regex parsing — Pydantic model enforced by LiteLLM.
        Uses light-chat (qwen3:0.6b) — normalization is a cheap, repetitive task.
        """
        start_time = time.perf_counter()

        # ── Heuristic pre-check (zero-cost, sub-ms) ───────────────────────────
        stripped = query.strip()
        if len(stripped) < 3 or stripped.replace(" ", "").isdigit():
            logfire.info("normalize_query: rejected by heuristic (too short / digit-only)")
            return NormalizedQuery(
                canonical=stripped,
                detected_language="unknown",
                intent="OTHER",
                extracted_keywords=[],
                is_valid=False,
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a query preprocessing assistant for a bilingual "
                    "(Vietnamese/English) SME product database.\n"
                    "IMPORTANT: Vietnamese (vi) and English (en) are BOTH valid "
                    "languages. Queries about prices, specs, features, availability, "
                    "or comparing products are ALWAYS valid (is_valid=true).\n"
                    "Only set is_valid=false for clear spam, random characters, or "
                    "content completely unrelated to products (e.g. 'asdfg', "
                    "'tell me a joke').\n\n"
                    "Normalize the user query:\n"
                    "- canonical: full clean question preserving the user's intent\n"
                    "- detected_language: vi | en | mixed\n"
                    "- intent: INFO_QUERY | PRICING | COMPARISON | COMPLAINT | "
                    "NEGOTIATION | AVAILABILITY | OTHER\n"
                    "- extracted_keywords: up to 10 search keywords\n"
                    "- is_valid: true unless spam/gibberish/off-topic\n\n"
                    "Examples:\n"
                    "Q: 'Giá Samsung S24 Ultra?' → is_valid=true, lang=vi, "
                    "intent=PRICING\n"
                    "Q: 'iPhone 15 Pro Max specs?' → is_valid=true, lang=en, "
                    "intent=INFO_QUERY\n"
                    "Q: 'xzxzxz' → is_valid=false\n\n"
                    "Respond only in the required JSON schema."
                ),
            },
            {"role": "user", "content": query},
        ]
        try:
            response = await ai_router.acompletion(
                model="economy-chat",  # Same as generate_answer — no Ollama swap
                messages=messages,
                response_format=NormalizedQuery,
                temperature=0,
            )
            latency = time.perf_counter() - start_time
            logfire.info("normalize_query: {latency:.4f}s", latency=latency)
            content = response.choices[0].message.content
            return NormalizedQuery.model_validate_json(content)
        except Exception as exc:
            latency = time.perf_counter() - start_time
            logfire.error(
                "normalize_query failed: {err} ({latency:.4f}s)",
                err=str(exc),
                latency=latency,
            )
            # Graceful fallback — return minimal normalised form without
            # blocking retrieval
            return NormalizedQuery(
                canonical=query.strip(),
                detected_language="unknown",
                intent="OTHER",
                extracted_keywords=query.strip().split()[:10],
                is_valid=True,  # assume valid on error to avoid false rejects
            )
