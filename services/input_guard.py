"""Why this exists: nothing screened customer messages before they reached the
router/answer LLMs — abusive or unsafe requests (and attempts to make the bot produce
them) were handled only by the sales system prompt.
What it does: one Llama Guard classification per customer message (chat-completions
"safe" / "unsafe\\nS<n>,..." protocol), called directly through LiteLLM — NOT through
the AIGateway router, whose economy-chat fallback would answer instead of classify.
Fail-open: a guard outage/timeout never blocks a customer.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from core.config import settings

logger = logging.getLogger(__name__)

GUARD_REFUSAL_MESSAGE = (
    "Dạ, em xin phép không hỗ trợ nội dung này ạ. Em có thể giúp anh/chị tư vấn "
    "sản phẩm điện tử, báo giá hoặc đặt hàng — anh/chị đang quan tâm sản phẩm nào ạ?"
)


@dataclass
class GuardVerdict:
    safe: bool
    categories: list[str] = field(default_factory=list)
    checked: bool = False  # False = guard disabled or unavailable (fail-open)


def parse_verdict(text: str | None) -> GuardVerdict:
    """Parse Llama Guard output: 'safe' or 'unsafe' + newline + 'S1,S10'."""
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    if not lines:
        return GuardVerdict(safe=True, checked=False)
    head = lines[0].lower()
    if head == "safe":
        return GuardVerdict(safe=True, checked=True)
    if head == "unsafe":
        cats = [c.strip() for c in lines[1].split(",")] if len(lines) > 1 else []
        return GuardVerdict(safe=False, categories=[c for c in cats if c], checked=True)
    logger.warning("input guard returned unparseable output: %r", text[:80] if text else text)
    return GuardVerdict(safe=True, checked=False)


async def check_input(user_message: str | None) -> GuardVerdict:
    """Classify one customer message. Never raises; fail-open on any error."""
    if not settings.INPUT_GUARD_ENABLED or not (user_message or "").strip():
        return GuardVerdict(safe=True, checked=False)
    try:
        import litellm

        resp = await asyncio.wait_for(
            litellm.acompletion(
                model=settings.INPUT_GUARD_MODEL,
                messages=[{"role": "user", "content": user_message}],
                max_tokens=20,
                temperature=0,
            ),
            timeout=settings.INPUT_GUARD_TIMEOUT_S,
        )
        verdict = parse_verdict(resp.choices[0].message.content)
    except Exception as exc:
        logger.warning("input guard unavailable, failing open: %s", exc)
        return GuardVerdict(safe=True, checked=False)
    blocked = set(settings.INPUT_GUARD_BLOCK_CATEGORIES)
    if not verdict.safe and blocked and not (set(verdict.categories) & blocked):
        # Unsafe only in categories this shop does not block → let it through.
        return GuardVerdict(safe=True, categories=verdict.categories, checked=True)
    return verdict
