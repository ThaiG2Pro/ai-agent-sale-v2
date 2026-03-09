"""Unit tests for state_freshness_node (T062)."""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agent.nodes.state_freshness import state_freshness_validator_node


@pytest.fixture
def mock_db():
    mock = AsyncMock()
    # Ensure await db.execute returns a result object with scalar_one_or_none
    mock_result = MagicMock()
    mock.execute.return_value = mock_result
    return mock


@pytest.fixture
def mock_config(mock_db):
    return {"configurable": {"db": mock_db}}


@pytest.fixture
def initial_state():
    return {
        "session_id": "test-session",
        "order_info": {"product_id": "prod_123", "price": 100.0, "approved_price": 100.0},
    }


@pytest.mark.asyncio
async def test_state_freshness_out_of_stock(initial_state, mock_config, mock_db):
    """Test out of stock path (T062)."""
    mock_product = MagicMock()
    mock_product.stock_quantity = 0
    mock_db.execute.return_value.scalar_one_or_none.return_value = mock_product

    result = await state_freshness_validator_node(initial_state, mock_config)

    assert result.goto == "customer_support_node"
    assert result.update["hitl_rejection_reason"] == "out_of_stock"


@pytest.mark.asyncio
async def test_state_freshness_price_delta(initial_state, mock_config, mock_db):
    """Test price delta path > 5% (T062)."""
    mock_product = MagicMock()
    mock_product.stock_quantity = 10
    mock_product.price = Decimal("110.0")  # 10% increase
    mock_db.execute.return_value.scalar_one_or_none.return_value = mock_product

    result = await state_freshness_validator_node(initial_state, mock_config)

    assert result.goto == "hitl_guard_node"
    assert result.update["hitl_reason"] == "stale_data_price_change"
    assert result.update["order_info"]["approved_price"] == 110.0


@pytest.mark.asyncio
async def test_state_freshness_ok(initial_state, mock_config, mock_db):
    """Test success path (T062)."""
    mock_product = MagicMock()
    mock_product.stock_quantity = 10
    mock_product.price = Decimal("102.0")  # 2% increase, < 5%
    mock_db.execute.return_value.scalar_one_or_none.return_value = mock_product

    result = await state_freshness_validator_node(initial_state, mock_config)

    assert result.goto == "order_execution_node"
    assert result.update["hitl_freshness_valid"] is True
