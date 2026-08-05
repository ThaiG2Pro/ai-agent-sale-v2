"""Why this exists: LiteLLM configuration for the AI Sales Agent.
What it does: Sets up model routing and parameters for local and cloud models.
"""

from __future__ import annotations

import os
from typing import Any

from core.config import settings

# Article X: Model Selection Strategy
# Economy Tier: Local Ollama (qwen2.5, bge-small)
# Premium Tier: Cloud fallback (Groq/OpenAI)


# LiteLLM resolves provider API keys from os.environ. pydantic-settings loads
# .env into the Settings object only, so without this export a key set in .env
# is invisible when running outside docker compose (seed scripts, eval gate,
# bare uvicorn). setdefault: a real environment variable always wins.
for _key in ("GROQ_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
    _val = getattr(settings, _key, None)
    if _val:
        os.environ.setdefault(_key, _val)


def _litellm_params(model: str, **extra: Any) -> dict[str, Any]:
    """Build litellm_params for a model string.

    OLLAMA_BASE_URL must only be attached to local Ollama models — cloud
    model strings (e.g. "gemini/gemini-2.5-flash", "gpt-4o-mini") resolve
    their endpoint + API key from the environment via LiteLLM itself.
    """
    params: dict[str, Any] = {"model": model, **extra}
    if model.startswith("ollama/"):
        params["api_base"] = settings.OLLAMA_BASE_URL
        # Ollama silently truncates at its ~2k-4k default context — fatal for
        # RAG prompts (ADR-006). LiteLLM forwards num_ctx as options.num_ctx.
        params["num_ctx"] = settings.OLLAMA_NUM_CTX
    return params


LITELLM_CONFIG = {
    "model_list": [
        # ── Light tier: fast, cheap — normalization, keyword extraction ──────
        {
            "model_name": "light-chat",
            "model_info": {"id": "light-chat-local"},
            "litellm_params": _litellm_params(settings.LIGHT_CHAT_MODEL, stream=False),
        },
        # ── Economy tier: general tasks — normalize_query + RAG generation ──
        # Same model as normalize_query to avoid Ollama model swap mid-request
        {
            "model_name": "economy-chat",
            "model_info": {"id": "economy-chat-local"},
            "litellm_params": _litellm_params(settings.CHAT_MODEL, stream=True),
        },
        # economy-embedding is appended below — omitted entirely when
        # EMBED_MODEL is "local/<name>" (in-process fastembed): the LiteLLM
        # Router validates providers at init and rejects the local/ prefix,
        # and AIGateway.embed short-circuits before the router in that mode.
        # ── Powerful tier: deep reasoning — escalation, complex queries ──────
        {
            "model_name": "premium-local-chat",
            "model_info": {"id": "premium-local-deepseek"},
            "litellm_params": _litellm_params(settings.POWERFUL_CHAT_MODEL, stream=True),
        },
        # qwen3-4b: alias used by PREMIUM_MODEL env var in dev environments
        {
            "model_name": "qwen3-4b",
            "model_info": {"id": "qwen3-4b-local"},
            "litellm_params": _litellm_params("ollama/qwen3-4b-q6", stream=False),
        },
        # ── Cloud fallback — when local unavailable ───────────────────────────
        {
            "model_name": "premium-chat",
            "model_info": {"id": "premium-chat-groq"},
            "litellm_params": {
                # llama-3.1-70b-versatile was decommissioned by Groq; 3.3 is the
                # current versatile tier (verified against /v1/models).
                "model": "groq/llama-3.3-70b-versatile",
            },
        },
    ],
    # simple-shuffle avoids registering a global lowest-latency callback that
    # crashes when litellm is called directly (outside the router) with no
    # litellm_params context.
    "routing_strategy": "simple-shuffle",
}

if not settings.EMBED_MODEL.startswith("local/"):
    LITELLM_CONFIG["model_list"].append(
        {
            "model_name": "economy-embedding",
            "model_info": {"id": "economy-embedding-local"},
            "litellm_params": _litellm_params(settings.EMBED_MODEL),
        }
    )
