"""
Why this exists: v3-0 P3 (T09) — the stack had zero runtime resilience: no
retry/timeout tuning, `cooldown_time: 0`, and a single hardcoded fallback to
economy-chat; one hung Groq call hung the whole turn, and a free-tier 429
(which lasts hours) was retried blindly.
What it does: The intent-aware fallback ladder
    Groq 70b (premium) → Groq 8b → Ollama local → cache-only → holding+queue
with a per-rung 429 cooldown registry, an app-side daily token-budget gate
(one Postgres row per day/model — Groq exposes no daily counter), a turn
deadline, an in-process backpressure semaphore, and a degraded-turn counter
for /admin/metrics and the T12 alert loop. Kill switch: RESILIENCE_V3_ENABLED.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from core.config import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# T09: ORDER/NEGOTIATION/COMPLAINT never accept an answer from the local or
# cache rungs — a degraded turn on these intents is a 20% signal (2.3) and
# goes straight to holding + human queue.
RISKY_INTENTS = frozenset({"ORDER_PLACEMENT", "NEGOTIATION", "COMPLAINT"})

# Ladder rungs: (router alias, kind). "cloud" rungs are allowed for every
# intent; "local" only for non-risky intents. Cache-only and holding are not
# model rungs — the caller handles them from LadderResult.
_LADDER: tuple[tuple[str, str], ...] = (
    ("premium-chat", "cloud"),
    ("fallback-chat-8b", "cloud"),
    ("economy-chat", "local"),
)

# ── In-process state (demo scale — resets on restart, which is fine) ────────

# rung alias → unix timestamp until which it is cooling down (429 / auth).
_cooldowns: dict[str, float] = {}
# Degraded turns since process start / since last alert tick.
_degraded_count = 0
_degraded_since_alert = 0

# T09 backpressure: cap concurrent LLM turns in-process. Lazily created so
# tests and workers each get a semaphore bound to their own event loop.
_turn_semaphore: asyncio.Semaphore | None = None


def turn_semaphore() -> asyncio.Semaphore:
    global _turn_semaphore
    if _turn_semaphore is None:
        _turn_semaphore = asyncio.Semaphore(settings.LLM_MAX_CONCURRENT_TURNS)
    return _turn_semaphore


def reset_for_tests() -> None:
    """Test hook: clear cooldowns/counters/semaphore."""
    global _turn_semaphore, _degraded_count, _degraded_since_alert
    _cooldowns.clear()
    _degraded_count = 0
    _degraded_since_alert = 0
    _turn_semaphore = None


def record_degraded_turn() -> None:
    global _degraded_count, _degraded_since_alert
    _degraded_count += 1
    _degraded_since_alert += 1


def degraded_turn_count() -> int:
    return _degraded_count


def drain_degraded_since_alert() -> int:
    """T12 alert loop: degraded turns since the previous tick (then reset)."""
    global _degraded_since_alert
    n = _degraded_since_alert
    _degraded_since_alert = 0
    return n


def _rung_available(alias: str) -> bool:
    return _cooldowns.get(alias, 0.0) <= time.monotonic()


def _cool_down(alias: str, seconds: float) -> None:
    _cooldowns[alias] = time.monotonic() + seconds
    logger.warning("ladder: rung %s cooling down for %.0fs", alias, seconds)


def _classify_error(exc: Exception) -> str:
    """'rate_limit' | 'auth' | 'transient' — decides cooldown vs plain skip."""
    name = type(exc).__name__
    text = f"{name}: {exc}".lower()
    if "429" in text or "ratelimit" in text.replace(" ", ""):
        return "rate_limit"
    if "401" in text or "403" in text or "authentication" in text or "api key" in text:
        return "auth"
    return "transient"


# ── Token-budget gate (T09 3.3) ──────────────────────────────────────────────


def _today_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


async def add_token_usage(db: AsyncSession | None, model_alias: str, tokens: int) -> None:
    """Upsert today's token counter for a model. Best-effort — never raises."""
    if db is None or tokens <= 0 or not settings.RESILIENCE_V3_ENABLED:
        return
    try:
        from sqlalchemy.dialects.postgresql import insert

        from models.schema import LLMTokenBudget

        stmt = insert(LLMTokenBudget).values(day=_today_utc(), model=model_alias, tokens=tokens)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_llm_token_budget_day_model",
            set_={"tokens": LLMTokenBudget.tokens + tokens},
        )
        await db.execute(stmt)
        await db.flush()
    except Exception:
        logger.warning("token budget: usage write failed", exc_info=True)


async def premium_budget_exhausted(db: AsyncSession | None) -> bool:
    """True when today's premium usage is at/over DEGRADE_RATIO of the cap."""
    if db is None or not settings.RESILIENCE_V3_ENABLED:
        return False
    cap = settings.TOKEN_BUDGET_DAILY_PREMIUM
    if cap <= 0:
        return False
    try:
        from sqlalchemy import select

        from models.schema import LLMTokenBudget

        used = (
            await db.execute(
                select(LLMTokenBudget.tokens).where(
                    LLMTokenBudget.day == _today_utc(),
                    LLMTokenBudget.model == "premium-chat",
                )
            )
        ).scalar_one_or_none() or 0
        return used >= cap * settings.TOKEN_BUDGET_DEGRADE_RATIO
    except Exception:
        logger.warning("token budget: read failed — assuming budget OK", exc_info=True)
        return False


# ── The ladder ───────────────────────────────────────────────────────────────


@dataclass
class LadderResult:
    """Outcome of a ladder run.

    response is None ⇔ degraded is True: every allowed rung failed — the
    caller serves the cached answer (non-risky intents only) or the holding
    message + queue (risky intents / no cache).
    """

    response: Any | None
    model_used: str | None
    degraded: bool
    rungs_tried: list[str]


async def complete_with_ladder(
    messages: list[dict[str, str]],
    intent: str | None = None,
    db: AsyncSession | None = None,
    deadline: float | None = None,
    preferred_model: str | None = None,
    **kwargs: Any,
) -> LadderResult:
    """Run one completion down the intent-aware fallback ladder (3.1/3.2).

    deadline: time.monotonic() timestamp for the ~30s turn budget — when it
    has passed, remaining rungs are skipped and the result is degraded.
    preferred_model: when the caller did NOT ask for the premium tier (e.g.
    escalation chose economy), start the ladder at that rung instead.
    """
    from services.ai import AIGateway, extract_llm_metrics

    risky = (intent or "").upper() in RISKY_INTENTS
    rungs = list(_LADDER)
    aliases = [alias for alias, _ in rungs]
    if preferred_model:
        if preferred_model in aliases:
            # Start the ladder at the caller's rung (escalation already
            # decided economy — don't silently upgrade it to premium).
            rungs = rungs[aliases.index(preferred_model) :]
        else:
            rungs = [(preferred_model, "cloud"), *rungs]
    tried: list[str] = []
    top_alias = rungs[0][0]

    skip_premium = await premium_budget_exhausted(db)

    for alias, kind in rungs:
        if risky and kind != "cloud":
            # T09: risky intents never accept local answers.
            break
        if alias == "premium-chat" and skip_premium:
            logger.info("ladder: premium rung skipped (token budget ~exhausted)")
            continue
        if not _rung_available(alias):
            continue
        if deadline is not None and time.monotonic() >= deadline:
            logger.warning("ladder: turn budget exhausted before rung %s", alias)
            break

        tried.append(alias)
        try:
            response = await AIGateway.complete(
                messages=messages, model=alias, _ladder=True, **kwargs
            )
            metrics = extract_llm_metrics(response)
            await add_token_usage(db, alias, metrics.total_tokens)
            return LadderResult(
                response=response,
                model_used=alias,
                degraded=alias != top_alias,
                rungs_tried=tried,
            )
        except TimeoutError:
            logger.warning("ladder: rung %s timed out", alias)
            continue
        except Exception as exc:
            klass = _classify_error(exc)
            if klass == "rate_limit":
                _cool_down(alias, settings.LLM_429_COOLDOWN_S)
            elif klass == "auth":
                _cool_down(alias, 3600.0)
            else:
                logger.warning("ladder: rung %s failed (%s)", alias, exc)
            continue

    record_degraded_turn()
    return LadderResult(response=None, model_used=None, degraded=True, rungs_tried=tried)


HOLDING_MESSAGE = (
    "Dạ hệ thống đang hơi quá tải, shop đã ghi nhận yêu cầu của bạn và nhân viên "
    "sẽ phản hồi trong ~{minutes} phút. Cảm ơn bạn đã kiên nhẫn! 🙏"
)


def holding_message() -> str:
    return HOLDING_MESSAGE.format(minutes=settings.HITL_WAIT_ALERT_MINUTES)
