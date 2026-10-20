"""Tests for structured order mutation parsing (Pha 1)."""

import pytest

from core.agent.order_mutator import parse_order_mutation
from core.agent.schemas.mutation import OrderActionEnum


@pytest.mark.asyncio
async def test_parse_update_quantity_doi_thanh_1_con():
    """Verify 'đổi thành 1 con thôi đc không' maps to update_quantity, NOT change_product."""
    order = {"name": "POCO F6 256GB", "quantity": 2, "price": 8490000.0}
    text = "đổi thành 1 con thôi đc không?"
    res = await parse_order_mutation(text, current_order=order)
    assert res.action == OrderActionEnum.UPDATE_QUANTITY
    assert res.new_quantity == 1
    assert res.target_product_name is None


@pytest.mark.asyncio
async def test_parse_update_quantity_lay_2_cai():
    order = {"name": "POCO F6 256GB", "quantity": 1}
    text = "cho mình lấy 2 cái nhé"
    res = await parse_order_mutation(text, current_order=order)
    assert res.action == OrderActionEnum.UPDATE_QUANTITY
    assert res.new_quantity == 2
    assert res.target_product_name is None


@pytest.mark.asyncio
async def test_parse_confirm_urgency():
    order = {"name": "POCO F6 256GB", "quantity": 1}
    text = "duyệt chưa vậy nào xong báo tôi nhé"
    res = await parse_order_mutation(text, current_order=order)
    assert res.action in (OrderActionEnum.CONFIRM, OrderActionEnum.GENERAL_QUERY)
    assert not res.target_product_name


@pytest.mark.asyncio
async def test_parse_cancel():
    order = {"name": "POCO F6 256GB", "quantity": 1}
    text = "thôi không lấy nữa hủy giúp mình"
    res = await parse_order_mutation(text, current_order=order)
    assert res.action == OrderActionEnum.CANCEL


@pytest.mark.asyncio
async def test_parse_change_product_explicit():
    order = {"name": "POCO F6 256GB", "quantity": 1}
    text = "đổi sang Samsung S24 Ultra giúp mình"
    res = await parse_order_mutation(text, current_order=order)
    assert res.action == OrderActionEnum.CHANGE_PRODUCT
    assert res.target_product_name is not None
    assert "samsung" in res.target_product_name.lower() or "s24" in res.target_product_name.lower()


@pytest.mark.asyncio
async def test_extract_fulfillment_details_vietnamese():
    from core.agent.order_mutator import extract_fulfillment_details

    text = "tôi ở hồ chí minh, sdt 0566464459 lấy 2 máy nhé, ngân sách dưới 30 triệu"
    details = await extract_fulfillment_details(text)
    assert details.phone == "0566464459"
    assert details.address is not None
    assert "hồ chí minh" in details.address.lower() or "hcm" in details.address.lower()
    assert details.quantity == 2
    assert details.target_budget == 30000000.0
