"""
Why this exists: Context compression logic for cost estimation (Article VII).
What it does: Transforms full history into a minimal context representation for guards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.messages import BaseMessage


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
    """Heuristic: 4 characters per token (T072)."""
    if not text:
        return 0
    return len(text) // 4
