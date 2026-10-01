"""core/support/persona.py — contract the support graph nodes rely on."""

from __future__ import annotations

from core.support.persona import SPACELY_SUPPORT as P

_SHOP_WORDS = ("shop", "đặt hàng", "đơn hàng", "giữ hàng", "anh/chị", "sản phẩm điện tử")


def test_placeholders_present():
    assert "{candidate_note}" in P.clarify_system_prompt
    assert "{minutes}" not in P.holding_message  # support has no human-review ETA to promise


def test_no_shop_wording_leaks_into_customer_facing_text():
    for name in (
        "answer_system_prompt",
        "smalltalk_system_prompt",
        "smalltalk_fastpath_reply",
        "decline_message",
        "customer_cap_message",
        "clarify_system_prompt",
        "complaint_note",
        "holding_message",
    ):
        text = getattr(P, name).lower()
        for word in _SHOP_WORDS:
            assert word not in text, f"{name} contains shop wording {word!r}"


def test_router_prompt_lists_exactly_the_four_support_intents():
    prompt = P.router_system_prompt
    for intent in ("INFO_QUERY", "PRICING", "COMPLAINT", "SMALLTALK"):
        assert f"- {intent}:" in prompt
    assert "Never use ORDER_PLACEMENT" in prompt


def test_answer_prompt_guards():
    text = P.answer_system_prompt
    assert "KHÔNG bịa số liệu" in text
    assert "KHÔNG hứa hoàn tiền" in text
    assert "Tài liệu Spacely" in text and P.context_label == "Tài liệu Spacely"
