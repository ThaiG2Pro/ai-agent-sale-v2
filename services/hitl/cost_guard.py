"""
Why this exists: Context compression logic for cost estimation (Article VII).
What it does: Transforms full history into a minimal context representation for guards.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from core.config import settings

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage

logger = logging.getLogger(__name__)


def get_compressed_context_text(
    messages: list[BaseMessage],
    intent: str | None = None,
    order_info: dict[str, Any] | None = None,
) -> str:
    """Extracts high-signal info from state for token estimation (T072)."""
    # 1. Last 5 messages
    tail = messages[-5:]
    history_text = "\n".join([f"{m.type}: {m.content}" for m in tail])

    # 2. Intent and Product info
    product_name = (order_info or {}).get("name", "Unknown Product")
    meta_text = f"Intent: {intent or 'None'}\nProduct: {product_name}"

    return f"{meta_text}\n\nHistory:\n{history_text}"


def estimate_tokens_heuristic(text: str) -> int:
    """Heuristic: 4 characters per token (T072). Fallback when no tokenizer available."""
    if not text:
        return 0
    return len(text) // 4


def estimate_tokens(text: str, model: str | None = None) -> int:
    """Count tokens with the model's real tokenizer via litellm (FR cost guard).

    Vietnamese/Unicode text tokenizes at a very different ratio than the
    4-chars/token heuristic, so we prefer litellm.token_counter and only fall
    back to the heuristic when the tokenizer is unavailable (e.g. offline).
    """
    if not text:
        return 0
    try:
        import litellm

        return litellm.token_counter(model=model or settings.CHAT_MODEL, text=text)
    except Exception:
        logger.warning(
            "litellm.token_counter failed — falling back to 4-chars/token heuristic",
            exc_info=True,
        )
        return estimate_tokens_heuristic(text)
