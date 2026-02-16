"""Why this exists: LiteLLM configuration for the AI Sales Agent.
What it does: Sets up model routing and parameters for local and cloud models.
"""

from __future__ import annotations

from core.config import settings

# Article X: Model Selection Strategy
# Economy Tier: Local Ollama (qwen2.5, bge-small)
# Premium Tier: Cloud fallback (Groq/OpenAI)

LITELLM_CONFIG = {
    "model_list": [
        {
            "model_name": "economy-chat",
            "litellm_params": {
                "model": settings.CHAT_MODEL,
                "api_base": settings.OLLAMA_BASE_URL,
                "stream": True,
            },
        },
        {
            "model_name": "economy-embedding",
            "litellm_params": {
                "model": settings.EMBED_MODEL,
                "api_base": settings.OLLAMA_BASE_URL,
            },
        },
        # Premium fallback (Example: Groq llama3)
        {
            "model_name": "premium-chat",
            "litellm_params": {
                "model": "groq/llama-3.1-70b-versatile",
            },
        },
    ],
    "routing_strategy": "latency-based-routing",
}
