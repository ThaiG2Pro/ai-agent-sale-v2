# Contract: Inventory Lookup Tool

**Tool**: `inventory_lookup`  
**Location**: `core/agent/tools.py`  
**Status**: **STUB in Week 3** — returns mock data. Real ERP/inventory integration deferred.  
**Contract Tests**: `tests/contract/tools/test_inventory_tool_contract.py`

> **Why write the contract before the implementation?** Article IV (Integration-First Testing) + FR-009 mandate contract tests before tool logic. The contract defines the interface boundary so that when the real integration is built (Week 6+), zero refactoring of the agent state or nodes is required.

---

## Input Schema: InventoryLookupInput

```
InventoryLookupInput (Pydantic BaseModel, strict mode)
├── sku: str
│   Constraints: min_length=1, max_length=50, pattern=r"^[A-Z0-9_-]+$"
│   Description: "Product SKU to check stock level for"
└── warehouse_id: str | None
    Default: None
    Constraints: pattern=r"^[A-Z0-9]{3,10}$" if provided
    Description: "Optional warehouse filter (None = all warehouses)"
```

**Validation rules**:
- `sku` must be uppercase alphanumeric — prevents injection via SKU field (Article VI)
- `warehouse_id` optional; if provided, strict format check

---

## Output Schema: InventoryLookupOutput

```
InventoryLookupOutput (Pydantic BaseModel, strict mode)
├── sku: str                      # Echoed from input
├── stock_level: int              # Units available (≥ 0)
├── warehouse_id: str | None      # Which warehouse (None = aggregated)
├── available: bool               # True if stock_level > 0
└── error: str | None             # Non-None only if lookup failed gracefully
```

---

## Required Test Scenarios

### Scenario 1 — 200 OK: Valid SKU found
- **Input**: `InventoryLookupInput(sku="PROD-001")`
- **Mock**: Returns stock_level=50, available=True
- **Assert**: Output matches `InventoryLookupOutput` schema. `available=True`. `error=None`.

### Scenario 2 — 404 Not Found: SKU does not exist
- **Input**: `InventoryLookupInput(sku="UNKNOWN-SKU")`
- **Mock**: Returns HTTP 404 (or mock stub returns `stock_level=0`)
- **Assert**: `available=False`. `stock_level=0`. `error` is non-None with descriptive message. No exception propagates.

### Scenario 3 — 429 Too Many Requests
- **Input**: Valid SKU
- **Mock**: HTTP 429 with Retry-After header
- **Assert**: Graceful degradation. `error` contains rate-limit message. No exception propagates.

### Scenario 4 — 500 Server Error
- **Input**: Valid SKU
- **Mock**: HTTP 500
- **Assert**: `available=False`. `error` non-None. No exception propagates.

### Scenario 5 — ReadTimeout
- **Input**: Valid SKU
- **Mock**: `side_effect = httpx.ConnectTimeout`
- **Assert**: Tool returns within 5s timeout. `error` contains timeout message. `available=False`.

---

## Schema Drift Detection

Baseline stored at `tests/contract/tools/baselines/inventory_tool_baseline.json`. Structural diff on every test run.

**Schema-frozen commitment**: `InventoryLookupInput` and `InventoryLookupOutput` schemas are **frozen from Week 3 onward**. The Week 6 ERP integration MUST replace only the tool's internal implementation — adding/removing/renaming fields in either schema is a breaking change that requires a version bump and agent-state migration. The `error: str | None` field is intentionally generic — it is designed to cover all anticipated Week 6 failure modes including partial inventory data (`available=False`, `error="partial_data: only 2 of 3 warehouses responded"`), multi-warehouse aggregation failures, and ERP-specific errors. Week 6 implementers MUST encode failure detail into `error` rather than adding new fields.

---

## Stub Behavior (Week 3 only)

In Week 3, `inventory_lookup` always returns:
```json
{
  "sku": "<input_sku>",
  "stock_level": 99,
  "warehouse_id": null,
  "available": true,
  "error": null
}
```

The contract tests mock the **external call** even though Week 3 uses a stub. This ensures the test suite is valid when the real integration replaces the stub.

**Week 6 migration note**: When replacing the stub, implement using the `make_inventory_tool(db: AsyncSession)` factory closure pattern (see data-model.md §7). The stub in Week 3 has no factory because it needs no DB access, but the real ERP call in Week 6 likely will. Add the factory wrapper at that point — `AgentState` and node code require zero changes.
