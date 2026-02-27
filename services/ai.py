"""Why this exists: Centralized gateway for all AI model interactions.
What it does: Provides async wrappers for LiteLLM with fallback and latency tracking.
             Week 2 addition: NormalizedQuery schema + normalize_query() for
             query rewriting.
"""

from __future__ import annotations

import time
from typing import Any

import litellm
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
        description=(
            "The cleaned, canonical form of the user query in its original language."
        )
    )
    detected_language: str = Field(
        description="Detected language code, e.g. 'vi' or 'en'."
    )
    intent: str = Field(
        description=(
            "Primary intent: INFO_QUERY | PRICING | COMPARISON | "
            "COMPLAINT | NEGOTIATION | AVAILABILITY | OTHER"
        )
    )
    extracted_keywords: list[str] = Field(
        description=(
            "Up to 10 meaningful keywords extracted from the query for FTS enrichment."
        )
    )


# Initialize LiteLLM Router for advanced routing and fallbacks
ai_router = Router(**LITELLM_CONFIG)

# Enable OpenTelemetry/Logfire callbacks for LiteLLM (T011)
litellm.success_callback = ["logfire"]
litellm.failure_callback = ["logfire"]


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
            response = await ai_router.aembedding(
                model=model, input=input_text, **kwargs
            )

            latency = time.perf_counter() - start_time
            logfire.info(
                "AI Embedding finished: {model}, Latency: {latency:.4f}s",
                model=model,
                latency=latency,
            )

            # Extract embeddings from response
            return [data["embedding"] for data in response.data]

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
        """
        start_time = time.perf_counter()
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a query preprocessing assistant for a Vietnamese "
                    "SME product database.\n"
                    "Clean and normalize the user query:\n"
                    "- Remove filler words but preserve meaning\n"
                    "- Detect language (vi/en/mixed)\n"
                    "- Classify intent: INFO_QUERY | PRICING | COMPARISON | "
                    "COMPLAINT | NEGOTIATION | AVAILABILITY | OTHER\n"
                    "- Extract up to 10 keywords for full-text search\n"
                    "Respond only in the required JSON schema."
                ),
            },
            {"role": "user", "content": query},
        ]
        try:
            response = await ai_router.acompletion(
                model="economy-chat",
                messages=messages,
                response_format=NormalizedQuery,
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
            )
