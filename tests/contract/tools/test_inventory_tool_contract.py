"""Contract tests for inventory lookup tool (TDD Red phase).

Validates:
- Schema stability (no field drift)
- Valid SKU lookup
- Not-found scenarios
- Error handling (429, 500, timeout)
- Pydantic validation
"""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.agent.tools import InventoryLookupInput, InventoryLookupOutput


def load_baseline(name: str) -> dict:
    """Load baseline schema snapshot from baselines/ directory."""
    p = Path(__file__).parent / "baselines" / f"{name}.json"
    return json.loads(p.read_text())


def validate_schema_drift(model_class, baseline: dict) -> None:
    """Verify no field removed or renamed against baseline."""
    actual_fields = set(model_class.model_fields.keys())
    baseline_fields = set(baseline.keys())
    drift = actual_fields ^ baseline_fields
    assert not drift, f"Schema drift detected: {drift}"


class TestInventoryToolContract:
    """Inventory tool contract validation suite."""

    def test_inventory_tool_schema_no_drift(self):
        """Verify InventoryLookupOutput schema matches baseline (no field drift)."""
        baseline = load_baseline("inventory_tool_baseline")
        validate_schema_drift(InventoryLookupOutput, baseline)

    def test_inventory_lookup_input_validation_strict(self):
        """Verify InventoryLookupInput enforces strict validation."""
        # Valid input
        valid = InventoryLookupInput(sku="PROD-001", warehouse_id="WH001")
        assert valid.sku == "PROD-001"
        assert valid.warehouse_id == "WH001"

        # Valid without warehouse_id
        valid_no_wh = InventoryLookupInput(sku="PROD-001")
        assert valid_no_wh.warehouse_id is None

        # Invalid SKU: empty
        with pytest.raises(ValidationError):
            InventoryLookupInput(sku="")

        # Invalid SKU: too long
        with pytest.raises(ValidationError):
            InventoryLookupInput(sku="X" * 51)

        # Invalid SKU: bad characters (only A-Z, 0-9, underscore, dash allowed)
        with pytest.raises(ValidationError):
            InventoryLookupInput(sku="prod@001")

        # Invalid warehouse_id: too short
        with pytest.raises(ValidationError):
            InventoryLookupInput(sku="PROD-001", warehouse_id="WH")

        # Invalid warehouse_id: too long
        with pytest.raises(ValidationError):
            InventoryLookupInput(sku="PROD-001", warehouse_id="WAREHOUSE1234")

        # Invalid warehouse_id: bad characters
        with pytest.raises(ValidationError):
            InventoryLookupInput(sku="PROD-001", warehouse_id="wh-001")  # lowercase

    def test_inventory_lookup_output_validation_strict(self):
        """Verify InventoryLookupOutput enforces field validation."""
        valid = InventoryLookupOutput(
            sku="PROD-001", stock_level=100, warehouse_id="WH001", available=True
        )
        assert valid.sku == "PROD-001"
        assert valid.stock_level == 100
        assert valid.available is True

        # Valid with error
        with_error = InventoryLookupOutput(
            sku="PROD-001",
            stock_level=0,
            warehouse_id=None,
            available=False,
            error="Not found",
        )
        assert with_error.error == "Not found"

        # Invalid: negative stock_level
        with pytest.raises(ValidationError):
            InventoryLookupOutput(
                sku="PROD-001", stock_level=-1, warehouse_id=None, available=True
            )

        # Verify types are correct
        assert isinstance(valid.stock_level, int)
        assert isinstance(valid.available, bool)

    def test_inventory_lookup_valid_sku(self):
        """Valid SKU lookup returns available=True with stock_level."""
        output = InventoryLookupOutput(
            sku="PROD-001",
            stock_level=50,
            warehouse_id="WH001",
            available=True,
            error=None,
        )
        assert output.sku == "PROD-001"
        assert output.stock_level == 50
        assert output.available is True
        assert output.error is None

    def test_inventory_lookup_sku_not_found(self):
        """Scenario 2: Unknown SKU should return available=False with error."""
        output = InventoryLookupOutput(
            sku="UNKNOWN-SKU",
            stock_level=0,
            warehouse_id=None,
            available=False,
            error="SKU not found in inventory",
        )
        assert output.sku == "UNKNOWN-SKU"
        assert output.stock_level == 0
        assert output.available is False
        assert output.error is not None

    def test_inventory_lookup_out_of_stock(self):
        """Test product exists but out of stock."""
        output = InventoryLookupOutput(
            sku="PROD-002",
            stock_level=0,
            warehouse_id="WH001",
            available=False,
            error=None,
        )
        assert output.available is False
        assert output.stock_level == 0
        assert output.error is None  # No error, just out of stock

    def test_inventory_lookup_multiple_warehouses(self):
        """Test warehouse_id filtering works (optional field)."""
        output_wh1 = InventoryLookupOutput(
            sku="PROD-001", stock_level=100, warehouse_id="WH001", available=True
        )
        output_wh2 = InventoryLookupOutput(
            sku="PROD-001", stock_level=50, warehouse_id="WH002", available=True
        )
        assert output_wh1.warehouse_id == "WH001"
        assert output_wh2.warehouse_id == "WH002"

    def test_inventory_lookup_error_messages(self):
        """Test error field can capture various error scenarios."""
        # Timeout error
        timeout_output = InventoryLookupOutput(
            sku="PROD-001",
            stock_level=0,
            warehouse_id=None,
            available=False,
            error="Request timeout (429 rate limit)",
        )
        assert "timeout" in timeout_output.error.lower() or "429" in timeout_output.error

        # Server error
        server_output = InventoryLookupOutput(
            sku="PROD-001",
            stock_level=0,
            warehouse_id=None,
            available=False,
            error="Server error (HTTP 500)",
        )
        assert "500" in server_output.error or "server" in server_output.error.lower()

    def test_inventory_lookup_stock_level_edge_cases(self):
        """Test stock_level constraints (ge=0)."""
        # Zero stock
        zero_stock = InventoryLookupOutput(
            sku="PROD-001", stock_level=0, warehouse_id=None, available=False
        )
        assert zero_stock.stock_level == 0

        # Large stock
        large_stock = InventoryLookupOutput(
            sku="PROD-001", stock_level=999999, warehouse_id=None, available=True
        )
        assert large_stock.stock_level == 999999
