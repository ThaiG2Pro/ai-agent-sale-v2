# Bulk Product Seeding Script Refactoring

## Summary of Changes

The `scripts/seed_bulk.py` script has been refactored to improve flexibility, testability, and alignment with the unified RAG ingestion pipeline.

### 1. **Configurable Product Categories** ✅

**Before:** Product categories were hardcoded as a Python list in the script.

**After:** Categories are now loaded from a JSON configuration file:
- **File location:** `scripts/product_categories.json`
- **Fallback:** If the JSON file is missing, the script uses minimal default categories.
- **Benefit:** Non-technical users can edit product topics without touching Python code.

#### Example `product_categories.json` structure:
```json
[
  {
    "category": "Điện tử & Công nghệ",
    "subcategories": ["Điện thoại", "Laptop", "Máy tính bảng"],
    "brands": ["Samsung", "Apple", "Xiaomi"],
    "price_range": [500000, 50000000]
  }
]
```

#### To customize:
1. Edit `scripts/product_categories.json`
2. Modify `category`, `subcategories`, `brands`, or `price_range` as needed
3. Re-run the seed script — it will pick up your changes automatically

---

### 2. **Database Selection (Test vs. Main)** ✅

**Before:** Script always wrote to the main `ai_agent` database.

**After:** New CLI flag `--test-db` allows choosing between databases:
```bash
# Seed main production database (default)
uv run python scripts/seed_bulk.py seed --total 1000

# Seed test database for evaluation/testing
uv run python scripts/seed_bulk.py seed --total 1000 --test-db
```

**Benefit:** Easy isolation of test data without affecting production database.

---

### 3. **Unified Ingestion Pattern** ✅

**Before:** Script used separate bulk insert + embedding logic not shared with RAG pipeline.

**After:** New function `ingest_products_via_unified_path()` provides two strategies:
- **Bulk path** (default): Optimized for large datasets — single SQL INSERT + batch embedding
- **Unified path** (optional): Uses `services/rag/ingest.py::ingest_product_text()` — same logic as main RAG pipeline

**Key benefit:** The bulk path is optimized for performance while the unified path ensures consistency with the main ingestion pipeline. Both follow the same schema.

#### Implementation:
```python
async def ingest_products_via_unified_path(
    products: list[ProductSeedItem],
    use_bulk: bool = True,  # Use optimized bulk path by default
) -> tuple[int, int]:
    """
    Returns: (products_ingested, keywords_extracted)
    """
```

---

### 4. **Schema Alignment** ✅

The refactored script now:
- Respects the updated `Product` schema with `metadata_` (JSONB) field
- Stores category, brand, and tags in `metadata_` field
- Aligns with `TextEmbedding` schema including `keywords` extraction
- Supports the same embedding governance (model_name, model_version, dimension tracking)

---

### 5. **Architecture Decisions**

#### Loading Categories at Module Level
```python
def _load_product_categories() -> list[dict[str, Any]]:
    # Checks scripts/product_categories.json at startup
    # Falls back to hardcoded defaults if file missing
    # Logs which source was used

PRODUCT_CATEGORIES: list[dict[str, Any]] = _load_product_categories()
```

**Rationale:**
- Loads once at module import (not per-call)
- Non-blocking: file I/O happens before async pipeline
- Clear logging: users see whether config was loaded or defaults used

---

## Usage Examples

### Basic seeding (main database)
```bash
uv run python scripts/seed_bulk.py seed --total 1000
```

### Dry run (validate data, no DB writes)
```bash
uv run python scripts/seed_bulk.py seed --total 100 --dry-run
```

### Seed test database
```bash
uv run python scripts/seed_bulk.py seed --total 1000 --test-db
```

### Scale to 10,000 products
```bash
uv run python scripts/seed_bulk.py seed --total 10000 --gen-batch 100 --embed-batch 64
```

### Incremental seeding (skip HNSW management)
```bash
uv run python scripts/seed_bulk.py seed --total 50 --skip-hnsw
```

---

## File Structure

```
scripts/
  seed_bulk.py                   # Refactored seeding script
  product_categories.json        # NEW: Customizable categories
```

---

## Breaking Changes

None. The script is fully backward compatible. All existing CLI flags work unchanged.

---

## Future Enhancements

1. **Category validation:** Schema validation for `product_categories.json`
2. **Database-specific templates:** Different category sets per environment
3. **Progress persistence:** Resume interrupted seeding operations
4. **Metrics export:** Save generation metrics (tokens used, cost estimates)

---

## Testing the Refactoring

```bash
# Test configuration loading
uv run python -c "
from scripts.seed_bulk import PRODUCT_CATEGORIES
print(f'Loaded {len(PRODUCT_CATEGORIES)} categories')
for cat in PRODUCT_CATEGORIES[:2]:
    print(f'  - {cat[\"category\"]}: {len(cat[\"subcategories\"])} subcategories')
"

# Test dry run
uv run python scripts/seed_bulk.py seed --total 10 --dry-run

# Test unified ingestion path (small scale)
uv run python scripts/seed_bulk.py seed --total 5
```

