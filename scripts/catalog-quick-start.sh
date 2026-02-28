#!/bin/bash
# Quick reference for file-based catalog ingestion

# ════════════════════════════════════════════════════════════════════════════════
# DEVELOPMENT WORKFLOW - Clean & Ingest
# ════════════════════════════════════════════════════════════════════════════════

# 1. CLEAN DATABASE (remove all product data)
echo "=== Step 1: Clean Database ===" 
uv run python scripts/cleanup_db.py --confirm

# 2. VALIDATE CATALOG (check if file is valid)
echo "=== Step 2: Validate Catalog ==="
uv run python scripts/ingest_catalog.py validate-catalog

# 3. PREVIEW INGESTION (dry-run, no DB writes)
echo "=== Step 3: Preview (Dry-Run) ==="
uv run python scripts/ingest_catalog.py ingest --dry-run

# 4. INGEST PRODUCTS (actually write to DB)
echo "=== Step 4: Ingest Products ==="
uv run python scripts/ingest_catalog.py ingest

# ════════════════════════════════════════════════════════════════════════════════
# TESTING WORKFLOW - Small Batch
# ════════════════════════════════════════════════════════════════════════════════

# Test with 5 products to test database
uv run python scripts/cleanup_db.py --test-db --confirm
uv run python scripts/ingest_catalog.py ingest --test-db --limit 5

# ════════════════════════════════════════════════════════════════════════════════
# CUSTOM CATALOG - Add Your Own Products
# ════════════════════════════════════════════════════════════════════════════════

# 1. Create new catalog file: scripts/my-products.json
# 2. Add products in the same format as product-catalog.json
# 3. Validate it:
uv run python scripts/ingest_catalog.py validate-catalog --catalog scripts/my-products.json

# 4. Ingest it:
uv run python scripts/ingest_catalog.py ingest --catalog scripts/my-products.json

# ════════════════════════════════════════════════════════════════════════════════
# PERFORMANCE TUNING
# ════════════════════════════════════════════════════════════════════════════════

# Reduce embedding concurrency (for low-resource machines)
uv run python scripts/ingest_catalog.py ingest --embed-concurrency 1

# Increase concurrency (for high-resource machines)
uv run python scripts/ingest_catalog.py ingest --embed-concurrency 8

# ════════════════════════════════════════════════════════════════════════════════
# HELP & DOCUMENTATION
# ════════════════════════════════════════════════════════════════════════════════

# Show cleanup options
uv run python scripts/cleanup_db.py --help

# Show ingest options
uv run python scripts/ingest_catalog.py ingest --help

# View full documentation
cat CATALOG_INGESTION.md
