"""Why this exists: model_traces already carries real token/cost/latency numbers
(WP3) but the SME has no way to see them, and nothing stops a cloud bill from
running away (WP-V2-5, plan §trục TIẾT KIỆM).
What it does: (1) aggregates model_traces for the GET /admin/costs dashboard
(by day / customer / model); (2) budget guard — daily USD ceiling that force-
downgrades to light-chat, and a per-customer daily LLM-call cap. Guards are
best-effort: a DB error never blocks a customer answer.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel
from sqlalchemy import Float, cast, func, select

from core.config import settings
from models.schema import ModelTrace

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

GROUP_BY_CHOICES = ("day", "customer", "model")


# ── Budget guard (answer path) ─────────────────────────────────────────────────


class BudgetStatus(BaseModel):
    """Outcome of the pre-LLM budget check for one turn."""

    over_daily_budget: bool = False
    over_customer_cap: bool = False
    daily_cost_usd: float = 0.0
    customer_calls_today: int = 0


def _utc_day_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


async def check_budget(customer_id: str | None, db: AsyncSession | None) -> BudgetStatus:
    """Evaluate both budget guards for the current turn. Never raises.

    Each guard only queries when its setting is enabled (> 0), so the default
    config (both 0) adds zero DB overhead to the answer path. On DB error the
    guard fails OPEN (availability first — a broken meter must not stop sales).
    """
    status = BudgetStatus()
    if db is None:
        return status
    day_start = _utc_day_start()

    if settings.DAILY_COST_LIMIT_USD > 0:
        try:
            total = await db.scalar(
                select(func.coalesce(func.sum(ModelTrace.cost), 0)).where(
                    ModelTrace.created_at >= day_start
                )
            )
            status.daily_cost_usd = float(total or 0)
            status.over_daily_budget = status.daily_cost_usd >= settings.DAILY_COST_LIMIT_USD
        except Exception:
            logger.error("Daily budget check failed — failing open", exc_info=True)

    if settings.CUSTOMER_DAILY_MSG_CAP > 0 and customer_id:
        try:
            count = await db.scalar(
                select(func.count(ModelTrace.id)).where(
                    ModelTrace.created_at >= day_start,
                    ModelTrace.metadata_["customer_id"].astext == customer_id,
                )
            )
            status.customer_calls_today = int(count or 0)
            status.over_customer_cap = (
                status.customer_calls_today >= settings.CUSTOMER_DAILY_MSG_CAP
            )
        except Exception:
            logger.error("Customer cap check failed — failing open", exc_info=True)

    return status


# ── Cost dashboard aggregation (GET /admin/costs) ──────────────────────────────


def _to_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _group_expression(group_by: str):
    """SQL expression producing the group key for one dashboard row."""
    if group_by == "day":
        return func.to_char(func.date_trunc("day", ModelTrace.created_at), "YYYY-MM-DD")
    if group_by == "customer":
        return func.coalesce(ModelTrace.metadata_["customer_id"].astext, "unknown")
    return ModelTrace.model_name


async def cost_report(
    db: AsyncSession,
    *,
    group_by: str,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate model_traces into the /admin/costs payload.

    Defaults to the last 7 days (UTC) when no range is given. cache_hit_rate =
    CACHE_HIT traces / all traces in the group (semantic cache hits are written
    as traces with guard_decision=CACHE_HIT and zero cost).
    """
    if group_by not in GROUP_BY_CHOICES:
        raise ValueError(f"group_by must be one of {GROUP_BY_CHOICES}")
    if date_from is None:
        date_from = _utc_day_start() - timedelta(days=6)
    if date_to is None:
        date_to = datetime.now(UTC)

    key = _group_expression(group_by).label("group_key")
    is_cache_hit = ModelTrace.metadata_["guard_decision"].astext == "CACHE_HIT"
    latency = cast(ModelTrace.latency_ms, Float)

    stmt = (
        select(
            key,
            func.count(ModelTrace.id).label("calls"),
            func.coalesce(func.sum(ModelTrace.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(ModelTrace.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(ModelTrace.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(ModelTrace.cost), 0).label("cost_usd"),
            func.percentile_cont(0.5).within_group(latency.asc()).label("latency_p50_ms"),
            func.percentile_cont(0.95).within_group(latency.asc()).label("latency_p95_ms"),
            func.count(ModelTrace.id).filter(is_cache_hit).label("cache_hits"),
        )
        .where(ModelTrace.created_at >= date_from, ModelTrace.created_at <= date_to)
        .group_by(key)
        .order_by(key)
    )
    rows = (await db.execute(stmt)).all()

    groups = []
    total_calls = 0
    total_cost = 0.0
    total_tokens = 0
    total_cache_hits = 0
    for row in rows:
        calls = int(row.calls)
        cache_hits = int(row.cache_hits)
        groups.append(
            {
                "key": row.group_key,
                "calls": calls,
                "prompt_tokens": int(row.prompt_tokens),
                "completion_tokens": int(row.completion_tokens),
                "total_tokens": int(row.total_tokens),
                "cost_usd": float(row.cost_usd),
                "latency_p50_ms": _to_float(row.latency_p50_ms),
                "latency_p95_ms": _to_float(row.latency_p95_ms),
                "cache_hits": cache_hits,
                "cache_hit_rate": round(cache_hits / calls, 4) if calls else 0.0,
            }
        )
        total_calls += calls
        total_cost += float(row.cost_usd)
        total_tokens += int(row.total_tokens)
        total_cache_hits += cache_hits

    return {
        "from": date_from.isoformat(),
        "to": date_to.isoformat(),
        "group_by": group_by,
        "totals": {
            "calls": total_calls,
            "total_tokens": total_tokens,
            "cost_usd": total_cost,
            "cache_hits": total_cache_hits,
            "cache_hit_rate": round(total_cache_hits / total_calls, 4) if total_calls else 0.0,
        },
        "groups": groups,
    }
