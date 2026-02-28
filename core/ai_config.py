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
        # ── Light tier: fast, cheap — normalization, keyword extraction ──────
        {
            "model_name": "light-chat",
            "model_info": {"id": "light-chat-local"},
            "litellm_params": {
                "model": settings.LIGHT_CHAT_MODEL,  # ollama/qwen3:0.6b
                "api_base": settings.OLLAMA_BASE_URL,
                "stream": False,
            },
        },
        # ── Economy tier: general tasks — metadata enrichment, RAG generation
        {
            "model_name": "economy-chat",
            "model_info": {"id": "economy-chat-local"},
            "litellm_params": {
                "model": settings.CHAT_MODEL,  # ollama/qwen3-4b-q6
                "api_base": settings.OLLAMA_BASE_URL,
                "stream": True,
            },
        },
        {
            "model_name": "economy-embedding",
            "model_info": {"id": "economy-embedding-local"},
            "litellm_params": {
                "model": settings.EMBED_MODEL,  # ollama/bge-m3
                "api_base": settings.OLLAMA_BASE_URL,
            },
        },
        # ── Powerful tier: deep reasoning — escalation, complex queries ──────
        {
            "model_name": "premium-local-chat",
            "model_info": {"id": "premium-local-deepseek"},
            "litellm_params": {
                "model": settings.POWERFUL_CHAT_MODEL,  # ollama/deepseek-r1
                "api_base": settings.OLLAMA_BASE_URL,
                "stream": True,
            },
        },
        # ── Cloud fallback — when local unavailable ───────────────────────────
        {
            "model_name": "premium-chat",
            "model_info": {"id": "premium-chat-groq"},
            "litellm_params": {
                "model": "groq/llama-3.1-70b-versatile",
            },
        },
    ],
    # simple-shuffle avoids registering a global lowest-latency callback that
    # crashes when litellm is called directly (outside the router) with no
    # litellm_params context.
    "routing_strategy": "simple-shuffle",
}
