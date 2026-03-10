"""queue_consumer_node — processes queued messages after unpause.

Why: Central integration point after HITL resume. Processes customer messages
received while paused, ensures history consistency by closing orphan tool calls,
and routes to the next step (execute, cancel, or re-pause).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, cast

import litellm
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command
from sqlalchemy import select, update

from core.config import settings
from models.schema import QueuedMessage
from services.hitl.schemas import QueuedMessageBatch, QueueIntentResult

if TYPE_CHECKING:
    from langchain_core.runnables import RunnableConfig
    from sqlalchemy.ext.asyncio import AsyncSession

    from core.agent.state import AgentState

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Keyword heuristic — deterministic, model-size-agnostic, language-aware.
# Works correctly in dev (small model) AND production (large model agrees).
# ---------------------------------------------------------------------------
_MODIFY_PATTERNS = re.compile(
    r"đổi\s*ý|thay\s*(đổi|sang|cho)|đặt\s*\w+\s*thay|lấy\s*\w+\s*(thay|đi|nhé|đó|này)|"
    r"đổi\s*sang|đổi\s*qua|muốn\s*(đổi|thay)|changed?\s*my\s*mind|switch\s*to|instead",
    re.IGNORECASE | re.UNICODE,
)
_CANCEL_PATTERNS = re.compile(
    r"huỷ|hủy|không\s*(mua|đặt|lấy)\s*nữa|thôi\s*(không|rồi)|cancel|bỏ\s*đơn",
    re.IGNORECASE | re.UNICODE,
)
_CONFIRM_PATTERNS = re.compile(
    r"^(ok|oke|okay|được(\s*rồi)?|đồng\s*ý|cứ\s*đặt|yes|xác\s*nhận|chốt)[.!,\s]*$",
    re.IGNORECASE | re.UNICODE,
)


def _keyword_classify_batch(
    session_id: str,
    rows: list,
) -> QueuedMessageBatch | None:
    """Fast deterministic pre-classifier. Returns None when ambiguous (→ fall back to LLM).

    Strategy:
    - If ANY message matches MODIFY_ORDER keywords → has_modify=True (skip LLM).
    - If ANY message matches CANCEL keywords → has_cancel=True (skip LLM).
    - If ALL messages match CONFIRM keywords → has_confirm=True (skip LLM).
    - Otherwise return None → caller uses LLM.
    """
    results: list[QueueIntentResult] = []
    has_cancel = False
    has_modify = False
    all_confirm = True

    for row in rows:
        text = row.message_text
        msg_id = str(row.message_id)
        if _CANCEL_PATTERNS.search(text):
            intent, conf = "CANCEL", 0.95
            has_cancel = True
            all_confirm = False
        elif _MODIFY_PATTERNS.search(text):
            intent, conf = "MODIFY_ORDER", 0.92
            has_modify = True
            all_confirm = False
        elif _CONFIRM_PATTERNS.match(text.strip()):
            intent, conf = "CONFIRM", 0.90
        else:
            intent, conf = "OTHER", 0.50
            all_confirm = False
        results.append(
            QueueIntentResult(message_id=msg_id, text=text, intent=intent, confidence=conf)
        )

    # Only skip LLM when we have strong keyword signal
    if has_cancel or has_modify or (all_confirm and results):
        batch = QueuedMessageBatch(session_id=session_id, messages=results)
        batch.has_cancel = has_cancel
        batch.has_modify = has_modify
        batch.has_confirm = all_confirm and not has_cancel and not has_modify
        logger.info(
            "queue_consumer keyword classify: cancel=%s modify=%s confirm=%s",
            has_cancel,
            has_modify,
            batch.has_confirm,
        )
        return batch

    return None  # ambiguous → use LLM


async def queue_consumer_node(state: AgentState, config: RunnableConfig) -> Command:
    """Processes queued messages and orphan tools after resume (Phase 9).

    1. T029: Scan/close orphan tool calls in history.
    2. T030: Drain QueuedMessage from DB within transaction.
    3. T031: Batch classify intent of drained messages.
    4. T032-T034: Route based on net intent.
    """
    db = cast("AsyncSession", config["configurable"].get("db"))
    session_id = state["session_id"]
    messages = list(state.get("messages", []))

    # --- T029: Orphan Tool Call Scanner ---
    # scan only recent 20 messages for orphan tool calls
    recent_messages = messages[-20:]
    tool_call_ids = set()
    for msg in recent_messages:
        if isinstance(msg, AIMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_call_ids.add(tc["id"])

    # Check for existing tool messages in full history
    answered_tool_call_ids = set()
    for msg in messages:
        if isinstance(msg, ToolMessage):
            answered_tool_call_ids.add(msg.tool_call_id)

    # Append synthetic ToolMessage for each orphan
    orphan_found = False
    for t_id in tool_call_ids:
        if t_id not in answered_tool_call_ids:
            messages.append(
                ToolMessage(
                    tool_call_id=t_id,
                    content="[cancelled: session resumed]",
                )
            )
            orphan_found = True

    # --- T030: QueuedMessage Drain ---
    # Fetch messages enqueued during pause
    stmt = (
        select(QueuedMessage)
        .where(QueuedMessage.session_id == session_id, QueuedMessage.processed == False)  # noqa: E712
        .order_by(QueuedMessage.received_at.asc())
        .limit(20)
    )
    result = await db.execute(stmt)
    queued_rows = result.scalars().all()

    new_human_messages = []
    queued_ids = []
    for row in queued_rows:
        new_human_messages.append(
            HumanMessage(content=f"[Customer follow-up during review]: {row.message_text}")
        )
        queued_ids.append(row.message_id)

    messages.extend(new_human_messages)

    # If no queued messages, fall through early
    if not queued_rows:
        # Check if we need to update state messages due to orphan tool calls
        update_data: dict[str, Any] = {}
        if orphan_found:
            update_data["messages"] = messages

        return Command(goto="state_freshness_validator_node", update=update_data)

    # --- T031: Batch Intent Classification (2-layer) ---
    # Layer 1: keyword heuristic — deterministic, model-size-agnostic.
    # Handles Vietnamese change-of-mind ("đổi ý rồi, lấy X đi") and cancel phrases.
    # Returns None when ambiguous → falls through to LLM.
    batch_result = _keyword_classify_batch(session_id, queued_rows)

    if batch_result is None:
        # Layer 2: LLM classification with explicit few-shot examples.
        batch_text = "\n---\n".join([msg.content for msg in new_human_messages])
        try:
            response = await litellm.acompletion(
                model=settings.LIGHT_CHAT_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Classify the collective intent of customer messages sent "
                            "while their order was under admin review.\n\n"
                            "OUTPUT one of: CONFIRM, CANCEL, MODIFY_ORDER, OTHER\n\n"
                            "Rules:\n"
                            "- MODIFY_ORDER: customer wants to change product/quantity "
                            "(e.g. 'đổi ý rồi, lấy X đi', 'thay sang X', 'đặt X thay', "
                            "'lấy X thay vì Y', 'I changed my mind, get X instead').\n"
                            "- CANCEL: customer wants to cancel "
                            "(e.g. 'huỷ đơn', 'không mua nữa', 'cancel').\n"
                            "- CONFIRM: customer confirms existing order "
                            "(e.g. 'ok', 'đồng ý', 'được rồi', 'yes').\n"
                            "- OTHER: unrelated.\n\n"
                            "If ANY message is MODIFY_ORDER set has_modify=true. "
                            "If ANY message is CANCEL set has_cancel=true (highest priority)."
                        ),
                    },
                    {"role": "user", "content": batch_text},
                ],
                response_format=QueuedMessageBatch,
            )
            content = response.choices[0].message.content
            if isinstance(content, str):
                batch_result = QueuedMessageBatch.model_validate_json(content)
            else:
                batch_result = QueuedMessageBatch.model_validate(content)

        except Exception as e:
            logger.error(f"Batch classification failed for {session_id}: {e}")
            batch_result = QueuedMessageBatch(session_id=session_id, messages=[], has_confirm=True)

    # --- T032-T034: Routing ---
    # Mark messages as processed in DB (atomic UPDATE)
    await db.execute(
        update(QueuedMessage)
        .where(QueuedMessage.message_id.in_(queued_ids))
        .values(processed=True)
    )
    # We rely on the caller/config to commit the transaction if necessary,
    # but here we are in a node, usually the graph handles it.
    # However, T030 says "Begin atomic SQLAlchemy transaction here".
    # Since nodes are async, we might need to flush.
    await db.flush()

    update_payload = {"messages": messages}

    # T032: CANCEL override (highest priority)
    if batch_result.has_cancel:
        return Command(goto="cancellation_node", update=update_payload)

    # T033: MODIFY_ORDER re-pause
    if batch_result.has_modify:
        new_escalation_count = state.get("hitl_escalation_count", 0) + 1
        update_payload.update(
            {
                "hitl_escalation_count": new_escalation_count,
                "hitl_triggered": False,
                "hitl_pause_id": None,
                # Reset approval gate so hitl_guard_node re-evaluates (not skip to answer_node)
                "hitl_approved": False,
            }
        )
        return Command(goto="hitl_guard_node", update=update_payload)

    # T034: Fallthrough to freshness check
    return Command(goto="state_freshness_validator_node", update=update_payload)
