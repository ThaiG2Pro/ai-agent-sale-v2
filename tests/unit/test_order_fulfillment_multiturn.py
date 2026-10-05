"""Unit tests for multi-turn order fulfillment flow."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.agent.intent_transitions import extract_address, extract_phone
from core.agent.nodes.confidence import confidence_node
from core.agent.nodes.hitl_guard import hitl_guard_node
from core.agent.nodes.router import router_node
from core.agent.state import HITLReasonEnum, IntentEnum


def test_extract_phone_various_formats():
    """Verify phone extraction across various Vietnamese formats."""
    assert extract_phone("0566464459") == "0566464459"
    assert extract_phone("sđt của tôi là 0566464459") == "0566464459"
    assert extract_phone("số điện thoại 0988.123.456") == "0988123456"
    assert extract_phone("Phone: +84 566 464 459") == "0566464459"
    assert extract_phone("0912-345-678") == "0912345678"
    assert extract_phone("chỉ có chữ không có số") is None


def test_extract_address_various_formats():
    """Verify address extraction across various formats."""
    assert extract_address("123 Lê Lợi, Quận 1, TP HCM") == "123 Lê Lợi, Quận 1, TP HCM"
    assert extract_address("địa chỉ: Chung cư Sunrise City, Q7") == "Chung cư Sunrise City, Q7"
    assert extract_address("giao về số 45 Nguyễn Huệ, Đà Nẵng") == "số 45 Nguyễn Huệ, Đà Nẵng"
    assert extract_address("ở 10 Trần Phú, Hà Nội") == "10 Trần Phú, Hà Nội"
    assert extract_address("xin chào shop") is None


@pytest.mark.asyncio
async def test_multiturn_order_flow_simulation():
    """Simulates 3-turn checkout flow:
    Turn 1: User requests product -> order_info created with missing phone & address -> Bot asks for phone + address
    Turn 2: User provides phone "0566464459" -> Router detects follow-up -> Confidence updates phone -> Bot asks for address
    Turn 3: User provides address "123 Lê Lợi, Q1, HCM" -> Router detects follow-up -> Confidence updates address -> HITL triggers pause
    """
    mock_db = AsyncMock()
    mock_db.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)
    mock_config = {"configurable": {"db": mock_db}}

    # Turn 1: User says "Tôi muốn đặt mua iPhone 15 Pro Max"
    turn1_state = {
        "session_id": "session-test-order",
        "user_message": "Tôi muốn đặt mua iPhone 15 Pro Max",
        "intent": "ORDER_PLACEMENT",
        "confidence_score": 0.95,
        "citations": [
            {
                "product_id": "prod-ip15",
                "sku": "IP15PM-512",
                "name": "iPhone 15 Pro Max 512GB",
            }
        ],
        "messages": [],
    }

    mock_product = MagicMock()
    mock_product.id = "prod-ip15"
    mock_product.price = 34990000.0
    mock_product.description = "iPhone 15 Pro Max 512GB chính hãng Apple"
    mock_db.execute.return_value.scalar_one_or_none = MagicMock(return_value=mock_product)

    res_turn1_conf = await confidence_node(turn1_state, mock_config)
    order_info = res_turn1_conf.get("order_info")
    assert order_info is not None
    assert order_info["name"] == "iPhone 15 Pro Max 512GB"
    assert order_info["phone"] is None
    assert order_info["address"] is None

    turn1_state["order_info"] = order_info
    turn1_hitl_cmd = await hitl_guard_node(turn1_state, mock_config)
    assert turn1_hitl_cmd.goto == "answer_node"
    assert "số điện thoại và địa chỉ nhận hàng" in turn1_hitl_cmd.update["response"]

    # Turn 2: User sends only phone "0566464459"
    turn2_state = {
        "session_id": "session-test-order",
        "user_message": "0566464459",
        "intent": "ORDER_PLACEMENT",
        "previous_intent": "ORDER_PLACEMENT",
        "order_info": order_info,
        "messages": [],
    }

    # Router check
    cmd_turn2_router = await router_node(turn2_state)
    assert cmd_turn2_router.goto == "confidence_node"
    assert cmd_turn2_router.update["intent"] == IntentEnum.ORDER_PLACEMENT.value

    # Confidence check
    res_turn2_conf = await confidence_node(turn2_state, mock_config)
    updated_order_turn2 = res_turn2_conf["order_info"]
    assert updated_order_turn2["phone"] == "0566464459"
    assert updated_order_turn2["address"] is None

    # HITL Guard check
    turn2_state["order_info"] = updated_order_turn2
    turn2_hitl_cmd = await hitl_guard_node(turn2_state, mock_config)
    assert turn2_hitl_cmd.goto == "answer_node"
    assert "0566464459" in turn2_hitl_cmd.update["response"]
    assert "địa chỉ nhận hàng" in turn2_hitl_cmd.update["response"]

    # Turn 3: User sends address "123 Lê Lợi, Quận 1, TP HCM"
    turn3_state = {
        "session_id": "session-test-order",
        "user_message": "123 Lê Lợi, Quận 1, TP HCM",
        "intent": "ORDER_PLACEMENT",
        "previous_intent": "ORDER_PLACEMENT",
        "confidence_score": 0.95,
        "order_info": updated_order_turn2,
        "messages": [],
    }

    # Router check
    cmd_turn3_router = await router_node(turn3_state)
    assert cmd_turn3_router.goto == "confidence_node"
    assert cmd_turn3_router.update["intent"] == IntentEnum.ORDER_PLACEMENT.value

    # Confidence check
    res_turn3_conf = await confidence_node(turn3_state, mock_config)
    updated_order_turn3 = res_turn3_conf["order_info"]
    assert updated_order_turn3["phone"] == "0566464459"
    assert updated_order_turn3["address"] == "123 Lê Lợi, Quận 1, TP HCM"

    # HITL Guard check -> High value order (>10M) triggers interrupt
    turn3_state["order_info"] = updated_order_turn3
    mock_db.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)
    with patch(
        "core.agent.nodes.hitl_guard.interrupt",
        return_value={"action": "approve", "admin_user_id": "admin1"},
    ) as mock_interrupt:
        with patch("litellm.token_counter", return_value=100):
            turn3_hitl_cmd = await hitl_guard_node(turn3_state, mock_config)
            mock_interrupt.assert_called_once()
            assert mock_interrupt.call_args[0][0]["reason"] == HITLReasonEnum.ORDER_APPROVAL
            assert turn3_hitl_cmd.goto == "queue_consumer_node"
            assert turn3_hitl_cmd.update["hitl_approved"] is True
