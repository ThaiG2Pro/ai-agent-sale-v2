"""Why this exists: answer_node grew to ~1000 lines mixing the path dispatcher with
DB persistence and template responses; every change risked an unrelated path.
What it does: the non-LLM side of answering — template/status responses (catalog
fallback, order follow-up, degraded holding message) and the per-turn writes
(model trace, semantic cache, episodic event). All best-effort: never raise.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import logfire
from sqlalchemy import insert

from models.schema import ModelTrace

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.agent.state import AgentState
    from services.ai import LLMUsageMetrics

logger = logging.getLogger(__name__)


async def generate_catalog_response(state: AgentState, db: AsyncSession) -> str | None:
    """SC01: catalog listing for a declined vague browse query, or None (→ decline).

    Data-driven (services.rag.catalog): categories from the SKU prefixes in the DB,
    brand/model words matched against real product names — no hardcoded brands.
    """
    from services.rag.catalog import build_catalog_response

    try:
        return await build_catalog_response(state.get("user_message", ""), db)
    except Exception as exc:
        logger.warning("catalog fallback failed: %s", exc)
        return None


async def generate_followup_response(state: AgentState, db: object) -> str:
    """Generate response for status follow-up queries (e.g. 'đặt chưa?')."""
    session_id = state.get("session_id")
    order_info = state.get("order_info")

    # 1. Check order_info in current state
    if order_info and isinstance(order_info, dict):
        status = order_info.get("status", "pending")
        name = order_info.get("name") or order_info.get("product_name") or "sản phẩm"
        qty = order_info.get("quantity", 1)
        if status == "confirmed":
            return (
                f"Dạ, đơn hàng **{name}** (Số lượng: {qty}) của anh/chị đã được hệ thống "
                f"xác nhận thành công rồi ạ! Mã đơn: `{session_id}`."
            )
        elif status == "pending":
            return (
                f"Dạ, yêu cầu đặt hàng **{name}** (Số lượng: {qty}) của anh/chị đã được ghi nhận "
                "và đang chờ duyệt từ nhân viên shop. Cảm ơn anh/chị đã kiên nhẫn ạ!"
            )

    # 2. Check DB records if available
    if db and session_id:
        from sqlalchemy import select

        from models.schema import HITLMetadata, Order

        # Check Order table
        ord_stmt = (
            select(Order)
            .where(Order.session_id == session_id)
            .order_by(Order.created_at.desc())
            .limit(1)
        )
        ord_res = (await db.execute(ord_stmt)).scalar_one_or_none()
        if ord_res:
            info = ord_res.order_info or {}
            pname = info.get("name") or info.get("product_name") or "sản phẩm"
            pqty = info.get("quantity", 1)
            code = info.get("order_id") or session_id
            # Report the row's REAL status — a cancelled/expired/under-review
            # order must never be announced as placed.
            status = ord_res.status
            if status == "confirmed":
                return (
                    f"Dạ, đơn hàng **{pname}** (Số lượng: {pqty}) của anh/chị đã được đặt "
                    f"thành công trên hệ thống rồi ạ! Mã đơn: `{code}`."
                )
            if status in ("draft", "pending_review"):
                return (
                    f"Dạ, đơn hàng **{pname}** (Số lượng: {pqty}) đang chờ nhân viên shop "
                    "xác nhận. Shop sẽ phản hồi ngay khi duyệt xong ạ!"
                )
            if status == "cancelled":
                return (
                    f"Dạ, đơn hàng **{pname}** đã được hủy trước đó ạ. Anh/chị có muốn "
                    "đặt lại hoặc xem sản phẩm khác không ạ?"
                )
            if status in ("expired", "superseded"):
                return (
                    f"Dạ, báo giá đơn **{pname}** đã hết hiệu lực. Anh/chị xác nhận lại "
                    "giúp em để shop lên đơn theo giá hiện tại nhé!"
                )

        # Check HITLMetadata table
        hitl_stmt = (
            select(HITLMetadata)
            .where(HITLMetadata.session_id == session_id)
            .order_by(HITLMetadata.paused_at.desc())
            .limit(1)
        )
        hitl_res = (await db.execute(hitl_stmt)).scalar_one_or_none()
        if hitl_res:
            if hitl_res.status in ("paused", "resuming"):
                return "Dạ, yêu cầu đặt hàng của anh/chị đang được nhân viên shop kiểm tra và xử lý. Shop sẽ phản hồi ngay khi hoàn tất ạ!"
            elif hitl_res.status == "approved":
                return f"Dạ, đơn hàng của anh/chị đã được phê duyệt thành công rồi ạ! Mã đơn: `{session_id}`."
            elif hitl_res.status == "rejected":
                return f"Dạ, đơn hàng của anh/chị chưa thể hoàn tất do: {hitl_res.pause_reason or 'chưa đủ điều kiện'}. Anh/chị có cần hỗ trợ gì khác không ạ?"

    return "Dạ, hiện tại shop chưa tìm thấy đơn hàng nào được khởi tạo trong phiên chat này. Anh/chị có muốn đặt mua sản phẩm nào không ạ?"


async def degraded_turn_response(state, db) -> dict:
    """v3-0 P3 (T09): every ladder rung failed for this turn.

    Non-risky intents may fall back to the cached answer (cache-only rung);
    risky intents — or no cache — get the holding message and land in the
    support queue so a human picks the turn up (degraded = 20% signal, 2.3).
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from models.schema import SupportQueue
    from services import resilience

    signals = [*(state.get("risk_signals") or []), "degraded"]
    intent = (state.get("intent") or "").upper()
    cached = state.get("cached_answer")

    if cached and intent not in resilience.RISKY_INTENTS:
        return {
            "response": cached,
            "model_used": "cache",
            "risk_signals": signals,
            "degraded": True,
        }

    if db is not None:
        try:
            await db.execute(
                pg_insert(SupportQueue)
                .values(
                    session_id=state["session_id"],
                    reason="degraded"[:50],
                    context_snapshot={
                        "user_message": state.get("user_message"),
                        "intent": state.get("intent"),
                        "risk_signals": signals,
                    },
                    status="pending",
                )
                .on_conflict_do_nothing(index_elements=["session_id"])
            )
            await db.flush()
        except Exception:
            logfire.warn("degraded turn: support_queue insert failed")

    return {
        "response": resilience.holding_message(),
        "model_used": None,
        "risk_signals": signals,
        "degraded": True,
    }


async def write_cache(state: AgentState, response: str, db: AsyncSession) -> None:
    """Write answer to semantic cache (L1+L2) after successful LLM generation."""
    try:
        from core.config import settings
        from services.rag.fragments import annotate_fragments
        from services.semantic_cache import set_cache

        citations_for_cache = []
        for c in state.get("citations") or []:
            if hasattr(c, "model_dump"):
                citations_for_cache.append(c.model_dump())
            elif isinstance(c, dict):
                citations_for_cache.append(c)

        # WP-V2-2 (FR-011): cached citations carry fragment_text so cache hits
        # replay fragment-level grounding.
        citations_for_cache = annotate_fragments(citations_for_cache, response)

        # Cache key stays canonical_query: in the graph path normalize is skipped
        # (intent pre-classified), so canonical_query == the pronoun-EXPANDED query
        # that get_l1_cache hashed — deterministic, and context-correct for pronoun
        # queries ("nó giá bao nhiêu" must not be cached across products).
        await set_cache(
            db=db,
            query=state["canonical_query"],
            response=response,
            embedding=state["query_vector"],
            model_name=settings.EMBED_MODEL,
            citations=citations_for_cache,
        )
    except Exception as exc:
        logger.warning("semantic cache write failed: %s", exc)


async def write_episodic_event(state: AgentState, response: str | None, db) -> None:
    """WP-V2-4: append this turn to the customer's episodic memory (best-effort).

    Skips SMALLTALK (no consultation content) and turns without customer/db.
    The service handles the EPISODIC_MEMORY_ENABLED kill switch and never raises.
    """
    customer_id = state.get("customer_id")
    if not db or not customer_id or state.get("intent") == "SMALLTALK":
        return
    from services.memory.episodic import EpisodicMemoryService

    await EpisodicMemoryService().record_event(
        customer_id=customer_id,
        thread_id=state.get("session_id", ""),
        user_message=state.get("user_message", ""),
        response=response,
        intent=state.get("intent"),
        citations=state.get("citations"),
        db=db,
    )


async def write_model_trace(
    state: AgentState,
    db: AsyncSession | None = None,
    metadata_: dict | None = None,
    metrics: LLMUsageMetrics | None = None,
) -> None:
    """Write model trace to agent_v1.model_traces table (T049).

    Called at end of answer_node for both accepted AND declined paths.
    `metrics` carries real token/cost/latency numbers from the LLM call;
    None (cache hit / declined / business path) writes zeros — correct,
    since no LLM call happened.
    Fail-safe: logs to stderr on error, doesn't block response.
    """
    if not db or not metadata_:
        return

    try:
        # WP-V2-5: stamp turn identity into the JSONB metadata so /admin/costs
        # can group by customer and the daily cap can count per-customer calls
        # (model_traces has no customer column — no migration needed this way).
        metadata_ = {
            **metadata_,
            "customer_id": state.get("customer_id"),
            "session_id": state.get("session_id"),
            "intent": state.get("intent"),
        }
        message_id = state.get("message_id")
        stmt = insert(ModelTrace).values(
            message_id=message_id,
            model_name=metadata_.get("intended_model") or "declined",
            prompt_tokens=metrics.prompt_tokens if metrics else 0,
            completion_tokens=metrics.completion_tokens if metrics else 0,
            total_tokens=metrics.total_tokens if metrics else 0,
            latency_ms=metrics.latency_ms if metrics else None,
            cost=metrics.cost if metrics else 0.00,
            metadata_=metadata_,
        )
        await db.execute(stmt)
        await db.commit()
    except Exception as e:
        logger.error("model trace write failed session_id=%s: %s", state.get("session_id"), e)
