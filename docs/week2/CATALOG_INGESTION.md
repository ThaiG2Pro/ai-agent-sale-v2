# File-Based Product Catalog Ingestion System

## Overview

This system provides two new scripts for managing product data in development and testing phases:

1. **`cleanup_db.py`** - Safely delete all data from database tables (schema preserved)
2. **`ingest_catalog.py`** - Load products from JSON catalog and ingest to database
3. **`product-catalog.json`** - Product data file with 19 sample tech products

## Architecture & Design

### Non-Blocking Async I/O
- All database operations use `async`/`await`
- File I/O executed in thread pool (`run_in_executor`) to avoid blocking event loop
- Semaphore-controlled embedding concurrency (default 4, configurable 1-16)
- Progress tracking with Rich library (non-blocking UI updates)

### Ingestion Pipeline
```
Load JSON Catalog
    ↓
Validate Pydantic Schema
    ↓
Batch Processing (configurable concurrency)
    ├─ Create Product record
    ├─ Generate embedding (async, semaphore-controlled)
    ├─ Enrich metadata (specs, category, keywords)
    ├─ Validate metadata vs source
    └─ Store TextEmbedding record
    ↓
Return statistics & metrics
```

### Data Alignment
- **File structure** (`product-catalog.json`): SKU, name, category, description, price, specs
- **Code structure** (`models/schema.py`): Product model with metadata storage
- **Ingestion path** (`services/rag/ingest.py`): Same pipeline used by RAG system

---

## Usage

### 1. Clean Database (Development Only)

```bash
# Show what will be deleted (confirm before proceeding)
uv run python scripts/cleanup_db.py

# Proceed without confirmation
uv run python scripts/cleanup_db.py --confirm

# Clean test database
uv run python scripts/cleanup_db.py --test-db --confirm
```

**What gets deleted (in order):**
- `text_embeddings`
- `conversation_messages`
- `conversation_sessions`
- `sales_signals`
- `model_traces`
- `products`

**Preserved:**
- Schema structure
- Alembic migration tracking

### 2. Validate Catalog File

```bash
# Check if catalog is valid (no DB writes)
uv run python scripts/ingest_catalog.py validate-catalog

# Validate custom catalog path
uv run python scripts/ingest_catalog.py validate-catalog --catalog /path/to/catalog.json
```

Output shows:
- Total product count
- All products in table format (SKU, Name, Category, Price)
- Validation errors (if any)

### 3. Ingest Products from Catalog

```bash
# Ingest all products to main database
uv run python scripts/ingest_catalog.py ingest

# Show what would be ingested (dry-run, no DB writes)
uv run python scripts/ingest_catalog.py ingest --dry-run

# Ingest to test database
uv run python scripts/ingest_catalog.py ingest --test-db

# Limit to first 10 products (for testing)
uv run python scripts/ingest_catalog.py ingest --limit 10

# Control concurrency (embed requests per second)
uv run python scripts/ingest_catalog.py ingest --embed-concurrency 2

# Combine options
uv run python scripts/ingest_catalog.py ingest --test-db --limit 5 --embed-concurrency 2
```

**Output metrics:**
- Progress bar with ETA
- Success/failed counts
- Throughput (products/second)
- Failed SKUs (if any)
- Total duration

---

## Catalog File Format

### Structure
```json
{
  "catalog": [
    {
      "sku": "PHONE-SM-001",
      "name": "Samsung Galaxy S24 Ultra 256GB",
      "category": "Điện thoại",
      "subcategory": "Smartphone",
      "price": 24990000,
      "currency": "VND",
      "description": "Detailed product description...",
      "intent": "B2C",
      "specifications": {
        "screen_size": "6.8 inch",
        "processor": "Snapdragon 8 Gen 3",
        ...
      }
    }
  ],
  "metadata": {
    "version": "1.0",
    "created_at": "2024-12-01",
    "total_products": 20
  }
}
```

### Field Specifications
| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `sku` | string | ✓ | 3-50 chars, unique identifier |
| `name` | string | ✓ | 3-500 chars, product title |
| `category` | string | ✓ | 2-100 chars (e.g., "Điện thoại") |
| `subcategory` | string | ✓ | 2-100 chars (e.g., "Smartphone") |
| `price` | float | ✓ | Must be > 0 |
| `currency` | string | ✗ | Default: "VND" |
| `description` | string | ✓ | 10-5000 chars, detailed text |
| `intent` | string | ✗ | Default: "B2C", use "B2B" for business |
| `specifications` | object | ✗ | Free-form key-value pairs |
| `metadata` | object | ✗ | Optional catalog-level metadata |

### Sample Product Categories
Current catalog includes:
- **Điện thoại** (Smartphones): iPhone, Samsung, Xiaomi
- **Laptop**: Dell XPS, ASUS VivoBook, MacBook Pro, Lenovo ThinkPad
- **Máy tính bảng** (Tablets): iPad Pro, Samsung Galaxy Tab
- **Phụ kiện** (Accessories): Smartwatches, Headphones, Power banks, Keyboards
- **Máy tính** (Computers): Monitors, GPU, SSD, RAM, PSU

---

## Development Workflow

### Phase 1: Setup
```bash
# 1. Clean existing data
uv run python scripts/cleanup_db.py --confirm

# 2. Validate new catalog
uv run python scripts/ingest_catalog.py validate-catalog

# 3. Preview ingestion (dry-run)
uv run python scripts/ingest_catalog.py ingest --dry-run
```

### Phase 2: Test Ingestion
```bash
# Ingest to test database with small batch
uv run python scripts/ingest_catalog.py ingest --test-db --limit 5
```

### Phase 3: Production Ingestion
```bash
# Ingest all products to main database
uv run python scripts/ingest_catalog.py ingest
```

---

## Performance Characteristics

### Ingestion Speed (Benchmarks)
- **Products/second**: ~0.5-1.5 products/sec (depends on embedding model)
- **Embedding generation**: ~200-500ms per product (local Ollama)
- **Metadata enrichment**: ~1-3 seconds per product (LLM-based)
- **Database operations**: <50ms per product (minimal overhead)

### Concurrency Settings
| Embed Concurrency | Use Case |
|------|----------|
| 1 | Low-resource dev machine, GPU memory limited |
| 2-4 | Standard local development (recommended) |
| 8-16 | High-resource machine, production ingest |

### Optimization Strategies
1. **L1 Cache**: Exact query hash matching (zero-cost for repeated queries)
2. **L2 Cache**: Semantic similarity search (avoid redundant embeddings)
3. **Batch Processing**: Bulk INSERT with ON CONFLICT idempotency
4. **Async Concurrency**: Semaphore-controlled embedding generation
5. **Off-loop I/O**: File reads in thread pool

---

## Error Handling

### Validation Errors
```
Catalog validation error:
  - field required (type=value_error.missing)
  - string too short (type=value_error.string.too_short)
```

### Ingestion Errors
- Individual product failures don't stop batch (fail-safe approach)
- Failed products listed at end with error message
- All metrics reported even with partial failures

### Common Issues

| Error | Cause | Solution |
|-------|-------|----------|
| `ModuleNotFoundError: core.db` | Wrong import | Uses `services.database.AsyncSessionLocal` |
| `FileNotFoundError: product-catalog.json` | Wrong path | Use `--catalog` flag or create file in `scripts/` |
| `ValidationError: price > 0` | Invalid price | Ensure all prices > 0 |
| `Connection refused` | DB not running | Start PostgreSQL: `docker-compose up -d postgres` |

---

## Integration with RAG System

### Same Ingestion Path
Both `seed_bulk.py` (LLM-generated) and `ingest_catalog.py` (file-based) use:
```python
from services.rag.ingest import ingest_product_text()
```

### Unified Pipeline
1. ✓ Client-side UUID7 generation
2. ✓ Embedding generation (async, batched)
3. ✓ Metadata enrichment (specs, category, keywords)
4. ✓ Hallucination detection (critic pattern)
5. ✓ TextEmbedding record with governance fields

---

## Future Enhancements

### Planned Features
- [ ] CSV/Excel import support
- [ ] Bulk product updates (not just inserts)
- [ ] Category validation against whitelist
- [ ] Price range constraints per category
- [ ] Duplicate detection (similar SKUs/names)
- [ ] Batch export to CSV/JSON
- [ ] Catalog versioning and rollback
- [ ] Performance metrics dashboard

### Performance Optimizations
- [ ] Vectorized embedding batch requests
- [ ] Connection pooling tuning
- [ ] HNSW index optimization for large catalogs (>10K products)
- [ ] Caching of enriched metadata

---

## Scripts Reference

### cleanup_db.py
```bash
uv run python scripts/cleanup_db.py [OPTIONS]

Options:
  --test-db      Target ai_agent_test instead of ai_agent
  --confirm      Skip confirmation prompt
```

### ingest_catalog.py
```bash
uv run python scripts/ingest_catalog.py COMMAND [OPTIONS]

Commands:
  ingest           Ingest products from catalog
  validate-catalog Validate catalog file

Ingest Options:
  --catalog PATH                Path to catalog JSON (default: scripts/product-catalog.json)
  --test-db                     Ingest to ai_agent_test
  --limit INT                   Limit to N products
  --embed-concurrency INT       Concurrent embeddings [1-16, default: 4]
  --dry-run                     Preview without DB writes
```

---

## File Structure

```
scripts/
├── cleanup_db.py              # Database cleaning script
├── ingest_catalog.py          # Catalog ingestion script
├── product-catalog.json       # Product data (20 samples)
├── product_categories.json    # Category definitions (from earlier refactor)
└── seed_bulk.py              # LLM-based generation (existing)

models/
└── schema.py                 # Product, TextEmbedding, etc.

services/
├── rag/
│   └── ingest.py            # Unified ingestion function
└── database.py              # AsyncSessionLocal
```

---

## See Also
- `SEED_REFACTORING.md` - Earlier refactoring of seed_bulk.py
- `UUIDV7_MIGRATION.md` - UUID v7 migration details
- `models/schema.py` - Product model definition
- `services/rag/ingest.py` - Ingestion pipeline implementation
