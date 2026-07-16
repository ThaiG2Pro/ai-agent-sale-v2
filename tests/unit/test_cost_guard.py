"""Unit tests for HITL cost guard token estimation (real tokenizer + fallback)."""

from unittest.mock import patch

from services.hitl.cost_guard import (
    estimate_tokens,
    estimate_tokens_heuristic,
    get_compressed_context_text,
)

VIETNAMESE_TEXT = (
    "Tôi muốn đặt mua hai chiếc điện thoại Samsung Galaxy S24 Ultra màu đen, "
    "giao hàng tận nơi tại Quận 7, Thành phố Hồ Chí Minh trước cuối tuần này nhé!"
)


def test_estimate_tokens_uses_litellm_token_counter():
    """estimate_tokens delegates to litellm.token_counter with the real text."""
    with patch("litellm.token_counter", return_value=42) as mock_counter:
        result = estimate_tokens(VIETNAMESE_TEXT)

    assert result == 42
    mock_counter.assert_called_once()
    assert mock_counter.call_args.kwargs["text"] == VIETNAMESE_TEXT
    assert mock_counter.call_args.kwargs["model"]  # a concrete model is passed


def test_estimate_tokens_vietnamese_differs_from_heuristic():
    """Vietnamese text: the real tokenizer count is used verbatim, not len//4."""
    heuristic = estimate_tokens_heuristic(VIETNAMESE_TEXT)
    # Vietnamese/Unicode typically yields MORE tokens than len//4 suggests.
    real_count = heuristic * 2
    with patch("litellm.token_counter", return_value=real_count):
        assert estimate_tokens(VIETNAMESE_TEXT) == real_count != heuristic


def test_estimate_tokens_falls_back_to_heuristic_on_error():
    """Tokenizer failure (e.g. offline) → 4-chars/token heuristic, no crash."""
    with patch("litellm.token_counter", side_effect=RuntimeError("offline")):
        assert estimate_tokens(VIETNAMESE_TEXT) == estimate_tokens_heuristic(VIETNAMESE_TEXT)


def test_estimate_tokens_empty_returns_zero():
    with patch("litellm.token_counter") as mock_counter:
        assert estimate_tokens("") == 0
    assert not mock_counter.called


def test_heuristic_handles_vietnamese():
    assert estimate_tokens_heuristic(VIETNAMESE_TEXT) == len(VIETNAMESE_TEXT) // 4
    assert estimate_tokens_heuristic("") == 0


def test_get_compressed_context_text_includes_tail_and_meta():
    class FakeMsg:
        def __init__(self, type_, content):
            self.type = type_
            self.content = content

    messages = [FakeMsg("human", f"msg {i}") for i in range(10)]
    text = get_compressed_context_text(
        messages, intent="ORDER_PLACEMENT", order_info={"name": "Galaxy S24"}
    )
    assert "Galaxy S24" in text
    assert "ORDER_PLACEMENT" in text
    assert "msg 9" in text
    assert "msg 0" not in text  # only last 5 messages kept
