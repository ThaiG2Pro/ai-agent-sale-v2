"""Why this exists: Centralized gateway for all AI model interactions.
What it does: Provides async wrappers for LiteLLM with fallback and latency tracking.
"""

from __future__ import annotations

import time
from typing import Any

import litellm
import logfire
from litellm import Router

from core.ai_config import LITELLM_CONFIG

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
