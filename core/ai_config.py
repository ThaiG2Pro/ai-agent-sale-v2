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


# Export all supported API keys to os.environ for LiteLLM
_API_KEY_NAMES = (
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "COHERE_API_KEY",
    "MISTRAL_API_KEY",
)

for _key in _API_KEY_NAMES:
    _val = getattr(settings, _key, None)
    if _val:
        os.environ.setdefault(_key, _val)


PROVIDER_KEY_MAP = {
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "cohere": "COHERE_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}


def _litellm_params(model_str: str, **extra: Any) -> dict[str, Any]:
    """Build litellm_params for any model string.

    Supports:
    1. Local llama-server (llama.cpp ROCm/CUDA/CPU):
       Model format: "hosted_vllm/<name>" or "openai/<name>" (when pointing to local llama-server)
       Example: "hosted_vllm/economy-chat", "hosted_vllm/qwen2.5-3b"
       Sets api_base = settings.LLAMA_SERVER_BASE_URL, custom_llm_provider = "hosted_vllm"

    2. Local Ollama:
       Model format: "ollama/<name>" or "ollama_chat/<name>"
       Example: "ollama/qwen2.5:3b", "ollama/bge-m3"
       Sets api_base = settings.OLLAMA_BASE_URL (or OLLAMA_EMBED_BASE_URL for embeddings)
       Sets num_ctx = settings.OLLAMA_NUM_CTX

    3. In-Process ONNX CPU Embedding (fastembed):
       Model format: "local/<name>"
       Example: "local/bge-m3" (short-circuited in AIGateway.embed)

    4. Cloud Provider Models (OpenAI, Groq, Gemini, Anthropic, OpenRouter, DeepSeek, Cohere, etc.):
       Model format: "groq/llama-3.3-70b-versatile", "gemini/gemini-2.5-flash", "gpt-4o-mini",
                     "anthropic/claude-3-5-sonnet-20241022", "openrouter/cheap", "deepseek/deepseek-chat"
       Passes model_str directly to LiteLLM, which uses the provider API key from os.environ.
       If provider API key is NOT set, automatically skips and falls back to settings.CHAT_MODEL.
    """
    # 1. Resolve app alias shortcuts if set in env (e.g. PREMIUM_MODEL=premium-chat)
    if model_str == "premium-chat":
        if settings.GROQ_API_KEY and settings.GROQ_API_KEY not in ("", "sk-no-key-required"):
            model_str = "groq/llama-3.3-70b-versatile"
        elif settings.GEMINI_API_KEY and settings.GEMINI_API_KEY not in ("", "sk-no-key-required"):
            model_str = "gemini/gemini-2.5-flash"
        elif settings.OPENAI_API_KEY and settings.OPENAI_API_KEY not in ("", "sk-no-key-required"):
            model_str = "gpt-4o-mini"
        else:
            model_str = settings.CHAT_MODEL

    # 2. Check if requested Cloud Provider has a valid API key configured
    if "/" in model_str:
        prefix = model_str.split("/", 1)[0].lower()
        if prefix in PROVIDER_KEY_MAP:
            key_attr = PROVIDER_KEY_MAP[prefix]
            key_val = getattr(settings, key_attr, None) or os.environ.get(key_attr)
            if not key_val or key_val in ("", "sk-no-key-required"):
                # Missing cloud key → automatically fallback to default local model
                if model_str != settings.CHAT_MODEL:
                    model_str = settings.CHAT_MODEL

    params: dict[str, Any] = {"model": model_str, **extra}

    # Bind provider API key explicitly from settings
    if "/" in model_str:
        prefix = model_str.split("/", 1)[0].lower()
        if prefix in PROVIDER_KEY_MAP:
            key_attr = PROVIDER_KEY_MAP[prefix]
            key_val = getattr(settings, key_attr, None) or os.environ.get(key_attr)
            if key_val and key_val not in ("", "sk-no-key-required"):
                params["api_key"] = key_val

    # v3-0 P3 (T09 timeout budget): per-call cap — local models get a longer
    # leash (25s) than cloud (15s); a hung call must never hang the turn.
    if settings.RESILIENCE_V3_ENABLED:
        is_local = model_str.startswith(("ollama/", "ollama_chat/", "hosted_vllm/"))
        params.setdefault(
            "timeout",
            settings.LLM_TIMEOUT_LOCAL_S if is_local else settings.LLM_TIMEOUT_CLOUD_S,
        )

    if model_str.startswith("hosted_vllm/"):
        target = model_str.removeprefix("hosted_vllm/")
        params["model"] = target
        params["api_base"] = settings.LLAMA_SERVER_BASE_URL
        params["custom_llm_provider"] = "hosted_vllm"
        params.setdefault("api_key", "sk-no-key-required")
    elif (
        model_str.startswith("openai/")
        and (
            "localhost" in settings.LLAMA_SERVER_BASE_URL
            or "llama-server" in settings.LLAMA_SERVER_BASE_URL
        )
        and settings.OPENAI_API_KEY == "sk-no-key-required"
    ):
        target = model_str.removeprefix("openai/")
        params["model"] = target
        params["api_base"] = settings.LLAMA_SERVER_BASE_URL
        params["custom_llm_provider"] = "hosted_vllm"
        params.setdefault("api_key", "sk-no-key-required")
    elif model_str.startswith("local/"):
        real_model = model_str.removeprefix("local/")
        params["model"] = f"ollama/{real_model}"
        params["api_base"] = settings.OLLAMA_EMBED_BASE_URL
        params["num_ctx"] = settings.OLLAMA_NUM_CTX

    return params


LITELLM_CONFIG = {
    "model_list": [
        # ── Light tier: fast, cheap (query normalization, keyword extraction) ──────
        {
            "model_name": "light-chat",
            "model_info": {"id": "light-chat-model"},
            "litellm_params": _litellm_params(settings.LIGHT_CHAT_MODEL, stream=False),
        },
        # ── Economy tier: general RAG & chat generation ────────────────────────────
        {
            "model_name": "economy-chat",
            "model_info": {"id": "economy-chat-model"},
            "litellm_params": _litellm_params(settings.CHAT_MODEL, stream=False),
        },
        # ── Powerful tier: deep reasoning / complex queries ─────────────────────────
        {
            "model_name": "powerful-chat",
            "model_info": {"id": "powerful-chat-model"},
            "litellm_params": _litellm_params(settings.POWERFUL_CHAT_MODEL, stream=False),
        },
        # Aliases for backward compatibility / dev configs
        {
            "model_name": "premium-local-chat",
            "model_info": {"id": "premium-local-deepseek"},
            "litellm_params": _litellm_params(settings.POWERFUL_CHAT_MODEL, stream=False),
        },
        {
            "model_name": "qwen3-4b",
            "model_info": {"id": "qwen3-4b-local"},
            "litellm_params": _litellm_params("ollama/qwen3-4b-q6", stream=False),
        },
        # ── Premium tier: cloud or escalated model ──────────────────────────────────
        {
            "model_name": "premium-chat",
            "model_info": {"id": "premium-chat-model"},
            "litellm_params": _litellm_params(settings.PREMIUM_MODEL, stream=False),
        },
        # ── v3-0 P3 (T09): middle rung of the fallback ladder (Groq 8b).
        # Without a GROQ key _litellm_params degrades this to CHAT_MODEL, so
        # the alias always resolves to something callable.
        {
            "model_name": "fallback-chat-8b",
            "model_info": {"id": "fallback-chat-8b-model"},
            "litellm_params": _litellm_params("groq/llama-3.1-8b-instant", stream=False),
        },
        # ── Universal Embedding Tier: dynamically configured via EMBED_MODEL ───────
        {
            "model_name": "economy-embedding",
            "model_info": {"id": "economy-embedding-model"},
            "litellm_params": _litellm_params(settings.EMBED_MODEL),
        },
    ],
    "routing_strategy": "simple-shuffle",
    # v3-0 P3 (T09): free-tier 429s persist for hours — cool the deployment
    # down instead of hammering it (pre-P3 value was 0 = no cooldown).
    "cooldown_time": 60 if settings.RESILIENCE_V3_ENABLED else 0,
}

# v3-0 P3 (T09): per-exception retry policy — transient network/timeout gets
# ONE retry; 429 and auth errors get ZERO (429 → cooldown + jump rung).
if settings.RESILIENCE_V3_ENABLED:
    try:
        from litellm.router import RetryPolicy

        LITELLM_CONFIG["retry_policy"] = RetryPolicy(
            TimeoutErrorRetries=1,
            RateLimitErrorRetries=0,
            AuthenticationErrorRetries=0,
            BadRequestErrorRetries=0,
            InternalServerErrorRetries=1,
        )
        LITELLM_CONFIG["num_retries"] = 0
    except Exception:  # pragma: no cover — older litellm without RetryPolicy
        LITELLM_CONFIG["num_retries"] = 0
