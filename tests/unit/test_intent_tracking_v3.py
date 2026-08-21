"""Unit tests for v3-0 P1 — multi-turn intent tracking combo (T03/T06/T08).

Covers: transition table + hysteresis, multi-intent hard priority (H6),
hesitation fast-path (F5), history-aware router with previous_intent (F1),
sticky-intent suppression of strange jumps, make_initial_state persistence,
queue_consumer F2 guard, and ambiguous-ORDER clarify (F4/O5).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agent.intent_transitions import (
    apply_transition,
    is_hesitation,
    normalize_priority,
)
from core.agent.nodes.confidence import _route_after_confidence, confidence_node
from core.agent.nodes.queue_consumer import _postvalidate_llm_batch
from core.agent.nodes.router import router_node
from core.agent.state import IntentClassification, IntentEnum, make_initial_state
from core.config import settings
from services.hitl.schemas import QueuedMessageBatch, QueueIntentResult


def _clf(primary, secondaries=(), confidence=0.9, shift=False):
    return IntentClassification(
        primary_intent=primary,
        secondary_intents=list(secondaries),
        confidence=confidence,
        reasoning="test",
        intent_shift=shift,
    )


def _llm_response(content: str):
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


# ---------------------------------------------------------------------------
# 1.1 — hesitation signals (T03 rec 2, F5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "thôi",
        "Thôi để xem thêm",
        "để xem thêm đã",
        "khoan đã",
        "chưa vội đâu",
        "để em suy nghĩ thêm",
        "mà khoan, để em nghĩ đã",
        "let me think about it",
    ],
)
def test_hesitation_positive(text):
    assert is_hesitation(text)


@pytest.mark.parametrize(
    "text",
    [
        "ok đặt luôn đi",
        "thôi được, cứ đặt nhé",
        "giá bao nhiêu vậy",
        "",
    ],
)
def test_hesitation_negative(text):
    assert not is_hesitation(text)


# ---------------------------------------------------------------------------
# 1.2 — multi-intent hard priority (T06, H6)
# ---------------------------------------------------------------------------


def test_priority_promotes_cancel_over_order():
    out = normalize_priority(_clf(IntentEnum.ORDER_PLACEMENT, [IntentEnum.CANCEL]))
    assert out.primary_intent == IntentEnum.CANCEL
    assert IntentEnum.ORDER_PLACEMENT in out.secondary_intents


def test_priority_promotes_complaint_over_info():
    out = normalize_priority(_clf(IntentEnum.INFO_QUERY, [IntentEnum.COMPLAINT]))
    assert out.primary_intent == IntentEnum.COMPLAINT


def test_priority_keeps_order_over_info_secondary():
    """H6: ORDER outranks INFO — primary stays, advisory part stays secondary."""
    out = normalize_priority(_clf(IntentEnum.ORDER_PLACEMENT, [IntentEnum.INFO_QUERY]))
    assert out.primary_intent == IntentEnum.ORDER_PLACEMENT
    assert out.secondary_intents == [IntentEnum.INFO_QUERY]


# ---------------------------------------------------------------------------
# 1.1 — transition table + hysteresis (T03 option 3)
# ---------------------------------------------------------------------------


def test_transition_no_previous_accepts():
    out, count = apply_transition(None, _clf(IntentEnum.INFO_QUERY), 0)
    assert out.primary_intent == IntentEnum.INFO_QUERY
    assert out.intent_shift is False
    assert count == 0


def test_transition_same_intent_no_shift():
    out, count = apply_transition("ORDER_PLACEMENT", _clf(IntentEnum.ORDER_PLACEMENT), 1)
    assert out.intent_shift is False
    assert count == 0


def test_transition_expected_order_to_cancel_modest_confidence():
    """ORDER→CANCEL is an expected transition — modest confidence suffices."""
    out, count = apply_transition("ORDER_PLACEMENT", _clf(IntentEnum.CANCEL, confidence=0.6), 0)
    assert out.primary_intent == IntentEnum.CANCEL
    assert out.intent_shift is True
    assert count == 0


def test_transition_strange_jump_midband_becomes_follow_up():
    """ORDER→SMALLTALK at 0.6 (strange, <0.7) → FOLLOW_UP of the previous intent."""
    out, count = apply_transition("ORDER_PLACEMENT", _clf(IntentEnum.SMALLTALK, confidence=0.6), 0)
    assert out.primary_intent == IntentEnum.FOLLOW_UP
    assert out.secondary_intents == [IntentEnum.SMALLTALK]
    assert count == 1


def test_transition_strange_jump_high_confidence_accepts():
    out, count = apply_transition("ORDER_PLACEMENT", _clf(IntentEnum.SMALLTALK, confidence=0.8), 0)
    assert out.primary_intent == IntentEnum.SMALLTALK
    assert out.intent_shift is True
    assert count == 0


def test_transition_low_confidence_keeps_previous():
    """Any differing intent below 0.5 → hysteresis keeps the previous intent."""
    out, count = apply_transition(
        "ORDER_PLACEMENT", _clf(IntentEnum.INFO_QUERY, confidence=0.3), 0
    )
    assert out.primary_intent == IntentEnum.ORDER_PLACEMENT
    assert IntentEnum.INFO_QUERY in out.secondary_intents
    assert out.intent_shift is False
    assert count == 1


def test_transition_escape_after_two_disagreements():
    """2 consecutive suppressed disagreements → accept the new intent."""
    out, count = apply_transition(
        "ORDER_PLACEMENT", _clf(IntentEnum.INFO_QUERY, confidence=0.3), 1
    )
    assert out.primary_intent == IntentEnum.INFO_QUERY
    assert out.intent_shift is True
    assert count == 0


# ---------------------------------------------------------------------------
# 1.1 — make_initial_state stops wiping intent channels
# ---------------------------------------------------------------------------


def test_make_initial_state_omits_intent_keys_when_enabled(monkeypatch):
    monkeypatch.setattr(settings, "INTENT_TRACKING_V3_ENABLED", True)
    state = make_initial_state("hello", "s1", "c1")
    assert "intent" not in state
    assert "secondary_intents" not in state
    assert "intent_disagreement_count" not in state
    assert state["intent_shift"] is False


def test_make_initial_state_wipes_intent_keys_when_disabled(monkeypatch):
    """Kill switch OFF → exact pre-v3-0 wipe-every-invoke behavior."""
    monkeypatch.setattr(settings, "INTENT_TRACKING_V3_ENABLED", False)
    state = make_initial_state("hello", "s1", "c1")
    assert state["intent"] is None
    assert state["secondary_intents"] == []


# ---------------------------------------------------------------------------
# Router — hesitation fast-path (F5) and sticky intent (F1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_hesitation_flip_from_in_flight_order(monkeypatch):
    """F5: 'khoan, để em nghĩ đã' with ORDER in flight → CANCEL, zero LLM calls."""
    monkeypatch.setattr(settings, "INTENT_TRACKING_V3_ENABLED", True)
    state = make_initial_state("mà khoan, để em nghĩ đã", "s-f5", "c1")
    state["intent"] = "ORDER_PLACEMENT"

    with patch("services.ai.AIGateway.complete", new_callable=AsyncMock) as mock_llm:
        command = await router_node(state)

    mock_llm.assert_not_called()
    assert command.goto == "cancellation_node"
    assert command.update["intent"] == "CANCEL"
    assert command.update["intent_shift"] is True


@pytest.mark.asyncio
async def test_router_hesitation_without_in_flight_order_goes_to_llm(monkeypatch):
    """Hesitation words with no order in flight must NOT flip to CANCEL."""
    monkeypatch.setattr(settings, "INTENT_TRACKING_V3_ENABLED", True)
    state = make_initial_state("để xem thêm sản phẩm khác", "s-f5b", "c1")

    clf = _clf(IntentEnum.INFO_QUERY, confidence=0.9)
    with patch(
        "services.ai.AIGateway.complete",
        new_callable=AsyncMock,
        return_value=_llm_response(clf.model_dump_json()),
    ):
        command = await router_node(state)

    assert command.update["intent"] == "INFO_QUERY"


@pytest.mark.asyncio
async def test_router_sticky_suppresses_strange_jump(monkeypatch):
    """F1-adjacent: ORDER in flight, LLM says SMALLTALK@0.6 → FOLLOW_UP (sticky)."""
    monkeypatch.setattr(settings, "INTENT_TRACKING_V3_ENABLED", True)
    state = make_initial_state("ờ ừm sao cũng được", "s-f1", "c1")
    state["intent"] = "ORDER_PLACEMENT"

    clf = _clf(IntentEnum.SMALLTALK, confidence=0.6)
    with patch(
        "services.ai.AIGateway.complete",
        new_callable=AsyncMock,
        return_value=_llm_response(clf.model_dump_json()),
    ):
        command = await router_node(state)

    assert command.update["intent"] == "FOLLOW_UP"
    assert command.update["intent_disagreement_count"] == 1
    assert command.goto == "memory_retrieval_node"


@pytest.mark.asyncio
async def test_router_prompt_includes_previous_intent_and_history(monkeypatch):
    """T03 option 1: the single classifier call carries history + previous intent."""
    monkeypatch.setattr(settings, "INTENT_TRACKING_V3_ENABLED", True)
    from langchain_core.messages import AIMessage, HumanMessage

    state = make_initial_state("thôi để xem thêm", "s-hist", "c1")
    state["intent"] = "PRICING"
    state["messages"] = [
        HumanMessage(content="laptop dưới 25 triệu có gì?"),
        AIMessage(content="Dạ có Vivobook Pro ạ"),
        HumanMessage(content="thôi để xem thêm"),
    ]

    clf = _clf(IntentEnum.INFO_QUERY, confidence=0.9)
    with patch(
        "services.ai.AIGateway.complete",
        new_callable=AsyncMock,
        return_value=_llm_response(clf.model_dump_json()),
    ) as mock_llm:
        await router_node(state)

    user_content = mock_llm.call_args.kwargs["messages"][1]["content"]
    assert "Previous turn intent: PRICING" in user_content
    assert "laptop dưới 25 triệu" in user_content
    assert user_content.rstrip().endswith("thôi để xem thêm")


@pytest.mark.asyncio
async def test_router_fallback_with_previous_intent_stays_sticky(monkeypatch):
    """LLM failure mid-conversation → hysteresis keeps the in-flight intent."""
    monkeypatch.setattr(settings, "INTENT_TRACKING_V3_ENABLED", True)
    state = make_initial_state("ship về Hà Nội nhé", "s-fb", "c1")
    state["intent"] = "ORDER_PLACEMENT"

    with patch(
        "services.ai.AIGateway.complete",
        new_callable=AsyncMock,
        side_effect=RuntimeError("ollama down"),
    ):
        command = await router_node(state)

    assert command.update["intent"] == "ORDER_PLACEMENT"


# ---------------------------------------------------------------------------
# Router — H6 mixed intent + priority promotion
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_mixed_info_order_queues_advisory_part(monkeypatch):
    """H6: 'laptop nào tốt… đặt luôn đi' → ORDER branch + advisory part queued."""
    monkeypatch.setattr(settings, "INTENT_TRACKING_V3_ENABLED", True)
    msg = "laptop nào tốt cho sinh viên, đặt luôn đi"
    state = make_initial_state(msg, "s-h6", "c1")

    clf = _clf(IntentEnum.ORDER_PLACEMENT, [IntentEnum.INFO_QUERY], confidence=0.9)
    with patch(
        "services.ai.AIGateway.complete",
        new_callable=AsyncMock,
        return_value=_llm_response(clf.model_dump_json()),
    ):
        command = await router_node(state)

    assert command.goto == "retrieval_node"
    assert command.update["intent"] == "ORDER_PLACEMENT"
    assert command.update["secondary_intents"] == ["INFO_QUERY"]
    assert command.update["pending_info_questions"] == msg


@pytest.mark.asyncio
async def test_router_promotes_cancel_from_secondary(monkeypatch):
    monkeypatch.setattr(settings, "INTENT_TRACKING_V3_ENABLED", True)
    state = make_initial_state("đặt thì đặt mà thôi chắc bỏ đi", "s-pri", "c1")

    clf = _clf(IntentEnum.ORDER_PLACEMENT, [IntentEnum.CANCEL], confidence=0.9)
    with patch(
        "services.ai.AIGateway.complete",
        new_callable=AsyncMock,
        return_value=_llm_response(clf.model_dump_json()),
    ):
        command = await router_node(state)

    assert command.goto == "cancellation_node"
    assert command.update["intent"] == "CANCEL"


# ---------------------------------------------------------------------------
# queue_consumer — F2 guard
# ---------------------------------------------------------------------------


def _row(message_id: str, text: str):
    return SimpleNamespace(message_id=message_id, message_text=text)


def test_postvalidate_remaps_out_of_enum_label_via_keywords():
    """F2: LLM label FOLLOW_UP for 'đổi ý' text → remapped to MODIFY_ORDER."""
    rows = [_row("m1", "Tôi đổi ý rồi, lấy Xiaomi đi")]
    batch = QueuedMessageBatch.model_construct(
        session_id="s1",
        messages=[
            QueueIntentResult.model_construct(
                message_id="m1",
                text="Tôi đổi ý rồi, lấy Xiaomi đi",
                intent="FOLLOW_UP",
                confidence=0.6,
            )
        ],
        has_cancel=False,
        has_modify=False,
        has_confirm=True,
        has_info=False,
        has_qty_change=False,
        has_product_change=False,
        has_negotiation=False,
        proposed_price=None,
    )
    out, force_review = _postvalidate_llm_batch(batch, rows)
    assert out.messages[0].intent == "MODIFY_ORDER"
    assert out.has_modify is True
    assert out.has_confirm is False
    assert force_review is False


def test_postvalidate_unresolvable_label_forces_review():
    rows = [_row("m1", "ưm cái đó ấy")]
    batch = QueuedMessageBatch.model_construct(
        session_id="s1",
        messages=[
            QueueIntentResult.model_construct(
                message_id="m1", text="ưm cái đó ấy", intent="FOLLOW_UP", confidence=0.5
            )
        ],
        has_cancel=False,
        has_modify=False,
        has_confirm=True,
        has_info=False,
        has_qty_change=False,
        has_product_change=False,
        has_negotiation=False,
        proposed_price=None,
    )
    out, force_review = _postvalidate_llm_batch(batch, rows)
    assert out.messages[0].intent == "OTHER"
    assert force_review is True


def test_postvalidate_rederives_flags_from_message_labels():
    """LLM labels a message CANCEL but forgets has_cancel → flags re-derived."""
    rows = [_row("m1", "không lấy nữa đâu")]
    batch = QueuedMessageBatch(
        session_id="s1",
        messages=[
            QueueIntentResult(
                message_id="m1", text="không lấy nữa đâu", intent="CANCEL", confidence=0.9
            )
        ],
        has_confirm=True,
    )
    out, force_review = _postvalidate_llm_batch(batch, rows)
    assert out.has_cancel is True
    assert out.has_confirm is False
    assert force_review is False


# ---------------------------------------------------------------------------
# confidence — ambiguous ORDER clarifies instead of auto-select (F4/O5)
# ---------------------------------------------------------------------------


def _order_state(**overrides):
    state = {
        "session_id": "s-f4",
        "user_message": "đặt cái đó đi",
        "intent": "ORDER_PLACEMENT",
        "similarity_score": 0.62,
        "similarity_gap": 0.01,
        "declined": False,
        "citations": [
            {"product_id": "p1", "sku": "SKU1", "name": "iPhone 15"},
            {"product_id": "p2", "sku": "SKU2", "name": "iPhone 15 Plus"},
        ],
        "memory_context": [],
    }
    state.update(overrides)
    return state


@pytest.mark.asyncio
async def test_confidence_ambiguous_order_clarifies(monkeypatch):
    monkeypatch.setattr(settings, "INTENT_TRACKING_V3_ENABLED", True)
    config = {"configurable": {"db": None}}
    result = await confidence_node(_order_state(), config)
    assert result["needs_clarification"] is True
    assert "order_info" not in result
    assert result["declined"] is False


@pytest.mark.asyncio
async def test_confidence_unambiguous_order_does_not_clarify(monkeypatch):
    """Big similarity gap → the top citation is the clear winner, no clarify."""
    monkeypatch.setattr(settings, "INTENT_TRACKING_V3_ENABLED", True)

    class _FakeResult:
        def scalar_one_or_none(self):
            return None

    class _FakeDB:
        async def execute(self, stmt):
            return _FakeResult()

    config = {"configurable": {"db": _FakeDB()}}
    result = await confidence_node(_order_state(similarity_gap=0.2), config)
    assert not result.get("needs_clarification")
    assert "order_info" in result


def test_route_ambiguous_order_to_clarify():
    state = _order_state(needs_clarification=True)
    assert _route_after_confidence(state) == "clarify_node"


def test_route_order_without_clarify_to_hitl_guard():
    state = _order_state(needs_clarification=False, confidence_score=0.8)
    assert _route_after_confidence(state) == "hitl_guard_node"
