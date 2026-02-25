"""Bulk data seeding script for the AI Sales Agent.

Why this exists: Populates the database with semantically rich product data for
  RAG, search, and agent evaluation without manual data entry.

What it does:
  1. Uses LLM (JSON-schema-forced) to generate semantically coherent product data.
  2. TypeAdapter for O(1) bulk validation + serialization.
  3. Raw asyncpg-backed bulk INSERT with ON CONFLICT for idempotency.
  4. Async-batched embedding generation (Semaphore-controlled concurrency).
  5. HNSW index drop/recreate around large ingest to avoid index bloat.
  6. Full OpenTelemetry tracing via logfire + structured logging.

Usage:
  uv run python scripts/seed_bulk.py --total 1000 --gen-batch 50 --embed-batch 32

Architecture Notes:
  - No ORM row-by-row inserts. Uses SQLAlchemy Core INSERT + asyncpg for perf.
  - ON CONFLICT (sku) DO NOTHING + RETURNING gives back only new rows.
  - Existing products/embeddings are detected and skipped (true idempotency).
  - HNSW dropped before bulk ingest; recreated CONCURRENTLY after commit.
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

# ── stdlib path hack so script runs from repo root ──────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import litellm
import logfire
import typer
from pydantic import BaseModel, Field, TypeAdapter, field_validator
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from core.config import settings
from core.logging import setup_logging
from models.schema import SCHEMA, Product, TextEmbedding
from services.database import AsyncSessionLocal, engine

console = Console()
app = typer.Typer(name="seed-bulk", help="Bulk product seeding with LLM + pgvector.")

# ── Vietnamese SME product categories ────────────────────────────────────────
PRODUCT_CATEGORIES: list[dict[str, Any]] = [
    {
        "category": "Điện tử & Công nghệ",
        "subcategories": ["Điện thoại", "Laptop", "Máy tính bảng", "Phụ kiện"],
        "brands": ["Samsung", "Apple", "Xiaomi", "ASUS", "Dell"],
        "price_range": (500_000, 50_000_000),
    },
    {
        "category": "Thời trang",
        "subcategories": ["Áo", "Quần", "Giày dép", "Túi xách", "Phụ kiện"],
        "brands": ["Biti's", "Canifa", "Ivy Moda", "Owen", "Routine"],
        "price_range": (100_000, 5_000_000),
    },
    {
        "category": "Gia dụng & Nội thất",
        "subcategories": ["Tủ lạnh", "Máy giặt", "Lò vi sóng", "Nồi cơm", "Ghế sofa"],
        "brands": ["Panasonic", "LG", "Toshiba", "Sunhouse", "Nội thất Hòa Phát"],
        "price_range": (200_000, 30_000_000),
    },
    {
        "category": "Sức khỏe & Làm đẹp",
        "subcategories": ["Mỹ phẩm", "Thực phẩm chức năng", "Dụng cụ tập gym", "Chăm sóc da"],
        "brands": ["L'Oréal", "The Face Shop", "Hana", "Murad", "Innisfree"],
        "price_range": (50_000, 3_000_000),
    },
    {
        "category": "Thực phẩm & Đồ uống",
        "subcategories": ["Đặc sản vùng miền", "Cà phê", "Trà", "Bánh kẹo", "Nước ngọt"],
        "brands": ["Vinamilk", "TH True Milk", "Trung Nguyên", "Phúc Long", "Kinh Đô"],
        "price_range": (15_000, 500_000),
    },
    {
        "category": "Đồ chơi & Trẻ em",
        "subcategories": ["Đồ chơi giáo dục", "Quần áo trẻ em", "Xe đạp trẻ em"],
        "brands": ["Lego", "Fisher-Price", "Chicco", "Đồ chơi Việt Nam"],
        "price_range": (50_000, 2_000_000),
    },
    {
        "category": "Thể thao & Dã ngoại",
        "subcategories": ["Giày thể thao", "Dụng cụ tập gym", "Đồ cắm trại"],
        "brands": ["Nike", "Adidas", "Puma", "Decathlon", "Hoka"],
        "price_range": (200_000, 8_000_000),
    },
    {
        "category": "Sách & Văn phòng phẩm",
        "subcategories": ["Sách kỹ năng", "Sách kinh tế", "Văn phòng phẩm", "Đồ dùng học tập"],
        "brands": ["NXB Trẻ", "NXB Kim Đồng", "Thiên Long", "Stabilo"],
        "price_range": (30_000, 500_000),
    },
]


# ═══════════════════════════════════════════════════════════════════════════
# § Pydantic Models (Validation + LLM schema enforcement)
# ═══════════════════════════════════════════════════════════════════════════


class ProductSeedItem(BaseModel):
    """Single product for seeding. Used for both LLM generation and DB insert."""

    sku: str = Field(
        ...,
        min_length=3,
        max_length=50,
        description="Unique product identifier, e.g. ELEC-PHONE-001",
    )
    name: str = Field(..., min_length=5, max_length=255, description="Product name in Vietnamese")
    description: str = Field(
        ...,
        min_length=30,
        description=(
            "Detailed product description in Vietnamese (at least 80 chars). "
            "Include key features, materials, usage. Semantically rich for RAG."
        ),
    )
    price: float = Field(..., gt=0, description="Price in Vietnamese Dong (VND)")
    category: str = Field(..., description="Product category")
    brand: str | None = Field(default=None, description="Brand name")
    tags: list[str] = Field(default_factory=list, description="Search tags")
    in_stock: bool = Field(default=True)

    @field_validator("sku")
    @classmethod
    def sku_must_be_uppercase(cls, v: str) -> str:
        return v.upper().strip()

    @field_validator("description")
    @classmethod
    def description_must_be_meaningful(cls, v: str) -> str:
        cleaned = v.strip()
        if len(cleaned) < 30:
            msg = "Description too short to be semantically meaningful"
            raise ValueError(msg)
        return cleaned


class ProductBatch(BaseModel):
    """Container for LLM-generated product batch — forces structured JSON output."""

    products: list[ProductSeedItem] = Field(
        ...,
        description="List of product objects to seed",
    )


# TypeAdapter for O(1) bulk validate + serialize — avoids looping Pydantic models
PRODUCT_LIST_ADAPTER: TypeAdapter[list[ProductSeedItem]] = TypeAdapter(list[ProductSeedItem])


# ═══════════════════════════════════════════════════════════════════════════
# § LLM Data Generation (JSON-schema forced, hallucination-resistant)
# ═══════════════════════════════════════════════════════════════════════════

# Build the JSON schema once at module level (avoid repeated reflection)
_PRODUCT_BATCH_SCHEMA: dict[str, Any] = ProductBatch.model_json_schema()


async def generate_product_batch(
    batch_size: int,
    category_config: dict[str, Any],
    start_index: int = 0,
) -> list[ProductSeedItem]:
    """Generate a batch of semantically coherent products via LLM.

    Uses response_format=ProductBatch to enforce JSON schema and prevent
    hallucination of invalid data structures.

    Args:
        batch_size: Number of products to generate.
        category_config: Category metadata (name, subcategories, brands, price_range).
        start_index: Offset for generating unique SKUs.

    Returns:
        Validated list of ProductSeedItem.
    """
    cat_name: str = category_config["category"]
    subcats: list[str] = category_config["subcategories"]
    brands: list[str] = category_config["brands"]
    price_lo, price_hi = category_config["price_range"]

    # Category prefix for SKU (e.g. "Điện tử" → "ELEC")
    cat_prefix = _category_to_sku_prefix(cat_name)

    system_prompt = textwrap.dedent(
        f"""Bạn là chuyên gia tạo dữ liệu sản phẩm thương mại điện tử Việt Nam.
        Nhiệm vụ: Tạo {batch_size} sản phẩm THỰC TẾ, ĐA DẠNG, có mô tả phong phú về ngữ nghĩa.

        RULES (BẮT BUỘC):
        - Danh mục: {cat_name}
        - Danh mục con (dùng lẫn lộn): {', '.join(subcats)}
        - Thương hiệu gợi ý (có thể sáng tạo thêm): {', '.join(brands)}
        - Khoảng giá (VND): {price_lo:,} - {price_hi:,}
        - SKU format: {cat_prefix}-<TYPE>-<3-digit-number>, ví dụ: {cat_prefix}-PHONE-001
        - Index bắt đầu từ: {start_index + 1} (dùng để tạo số cuối SKU không trùng)
        - description: PHẢI từ 80 ký tự trở lên, mô tả tính năng, chất liệu, công dụng bằng tiếng Việt
        - tags: 3-5 từ khóa ngắn liên quan
        - KHÔNG bịa đặt sku trùng nhau trong cùng batch
        - KHÔNG dùng placeholder như "product 1", "description here"
        """
    )

    user_prompt = f"Tạo chính xác {batch_size} sản phẩm theo schema yêu cầu."

    with logfire.span(
        "llm.generate_batch",
        category=cat_name,
        batch_size=batch_size,
        start_index=start_index,
    ):
        response = await litellm.acompletion(
            model=settings.CHAT_MODEL,
            api_base=settings.OLLAMA_BASE_URL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=ProductBatch,  # Force Pydantic JSON schema
            temperature=0.85,  # Some creativity but not too wild
            max_tokens=8192,
        )

    raw_content: str = response.choices[0].message.content  # type: ignore[union-attr]

    # Parse + validate with TypeAdapter (fast bulk path)
    try:
        parsed = json.loads(raw_content)
        batch_obj = ProductBatch.model_validate(parsed)
        # Extra bulk validation pass with TypeAdapter (catches edge case issues)
        validated = PRODUCT_LIST_ADAPTER.validate_python(
            [p.model_dump() for p in batch_obj.products]
        )
        logfire.info(
            "LLM batch generated: {count} products for category {cat}",
            count=len(validated),
            cat=cat_name,
        )
        return validated
    except Exception as exc:
        logfire.error(
            "LLM batch parse failed for {cat}: {err}",
            cat=cat_name,
            err=str(exc),
            raw_snippet=raw_content[:300],
        )
        return []


def _category_to_sku_prefix(category: str) -> str:
    """Map Vietnamese category name to ASCII SKU prefix."""
    mapping = {
        "Điện tử": "ELEC",
        "Thời trang": "FASH",
        "Gia dụng": "HOME",
        "Sức khỏe": "HLTH",
        "Thực phẩm": "FOOD",
        "Đồ chơi": "TOYS",
        "Thể thao": "SPRT",
        "Sách": "BOOK",
    }
    for key, prefix in mapping.items():
        if key in category:
            return prefix
    return "PROD"


# ═══════════════════════════════════════════════════════════════════════════
# § HNSW Index Management
# ═══════════════════════════════════════════════════════════════════════════


async def drop_hnsw_index() -> None:
    """Drop HNSW index before bulk ingest to avoid index bloat and slow writes.

    Safe to run even if the index doesn't exist (IF EXISTS).
    Uses a separate connection outside any transaction (required for CONCURRENTLY).
    """
    with logfire.span("hnsw.drop"):
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(
                text(
                    "DROP INDEX CONCURRENTLY IF EXISTS "
                    f"{SCHEMA}.idx_text_embeddings_embedding"
                )
            )
            logfire.info("HNSW index dropped (or did not exist).")


async def recreate_hnsw_index() -> None:
    """Recreate HNSW index after bulk ingest with optimized parameters.

    Parameters:
        m = 16         : links per node — good balance for SME-scale (< 10M rows)
        ef_construction= 64 : build-time quality (higher → better recall, slower build)
    Uses CONCURRENTLY to avoid locking the table.
    """
    with logfire.span("hnsw.recreate"):
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(
                text(
                    f"CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_text_embeddings_embedding "
                    f"ON {SCHEMA}.text_embeddings "
                    f"USING hnsw (embedding vector_cosine_ops) "
                    f"WITH (m = 16, ef_construction = 64)"
                )
            )
            logfire.info(
                "HNSW index recreated: m=16, ef_construction=64.",
            )


# ═══════════════════════════════════════════════════════════════════════════
# § Bulk Insert: Products
# ═══════════════════════════════════════════════════════════════════════════


async def bulk_insert_products(
    products: list[ProductSeedItem],
) -> list[dict[str, Any]]:
    """Bulk insert products using SQLAlchemy Core (single SQL statement).

    Strategy:
        - pg_insert (PostgreSQL-specific) for ON CONFLICT support.
        - ON CONFLICT (sku) DO NOTHING → idempotent, safe to rerun.
        - RETURNING id, sku → only new rows returned (existing rows skipped).
        - After INSERT, SELECT all requested SKUs to get IDs incl. pre-existing ones.

    Returns:
        List of dicts [{id: UUID, sku: str}] for ALL requested products.
    """
    skus = [p.sku for p in products]
    now = datetime.now(UTC)

    rows = [
        {
            "id": uuid.uuid4(),
            "sku": p.sku,
            "name": p.name,
            "description": p.description,
            "price": Decimal(str(p.price)),
            "metadata": {
                "category": p.category,
                "brand": p.brand,
                "tags": p.tags,
                "in_stock": p.in_stock,
                "seeded": True,
            },
            "created_at": now,
            "updated_at": now,
        }
        for p in products
    ]

    with logfire.span("db.bulk_insert_products", count=len(rows)):
        async with AsyncSessionLocal() as session:
            try:
                # Single bulk INSERT — O(1) round-trips, not O(N)
                stmt = (
                    pg_insert(Product)
                    .values(rows)
                    .on_conflict_do_nothing(index_elements=["sku"])
                )
                await session.execute(stmt)
                await session.commit()
                logfire.info(
                    "Products bulk-inserted (up to {n}, dupes skipped via ON CONFLICT).",
                    n=len(rows),
                )
            except Exception as exc:
                await session.rollback()
                logfire.error("Bulk insert products failed, rolled back: {err}", err=str(exc))
                raise

        # After insert: SELECT all the SKUs we care about to obtain their DB IDs
        # (RETURNING with ON CONFLICT DO NOTHING only returns new rows)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text(
                    f"SELECT id, sku FROM {SCHEMA}.products WHERE sku = ANY(:skus)"
                ),
                {"skus": skus},
            )
            rows_back = result.mappings().all()
            logfire.info("Fetched {n} product IDs after insert.", n=len(rows_back))
            return [dict(r) for r in rows_back]


# ═══════════════════════════════════════════════════════════════════════════
# § Embedding: Async Batched
# ═══════════════════════════════════════════════════════════════════════════


async def embed_texts_batched(
    texts: list[str],
    embed_batch_size: int = 32,
    max_concurrent: int = 3,
) -> list[list[float]]:
    """Generate embeddings for a list of texts using controlled async concurrency.

    Strategy:
        - Split texts into sub-batches of `embed_batch_size`.
        - Run up to `max_concurrent` embedding API calls concurrently (Semaphore).
        - Respects local Ollama server throughput limits.

    Args:
        texts: List of texts to embed.
        embed_batch_size: How many texts per API call.
        max_concurrent: Max simultaneous embedding API calls.

    Returns:
        List of embedding vectors (same order as input texts).
    """
    sem = asyncio.Semaphore(max_concurrent)
    batches = [texts[i : i + embed_batch_size] for i in range(0, len(texts), embed_batch_size)]
    all_vectors: list[list[float]] = [[] for _ in range(len(texts))]  # pre-allocate order-safe

    async def _embed_batch(batch_idx: int, batch: list[str]) -> tuple[int, list[list[float]]]:
        async with sem:
            with logfire.span(
                "embed.batch",
                batch_idx=batch_idx,
                size=len(batch),
                model=settings.EMBED_MODEL,
            ):
                response = await litellm.aembedding(
                    model=settings.EMBED_MODEL,
                    input=batch,
                    api_base=settings.OLLAMA_BASE_URL,
                )
                vectors = [item["embedding"] for item in response.data]
                logfire.info(
                    "Embedded batch {i}/{total}: {n} vectors.",
                    i=batch_idx + 1,
                    total=len(batches),
                    n=len(vectors),
                )
                return batch_idx, vectors

    tasks = [_embed_batch(i, b) for i, b in enumerate(batches)]
    results = await asyncio.gather(*tasks)

    # Reassemble in original order
    for batch_idx, vectors in results:
        start = batch_idx * embed_batch_size
        for j, vec in enumerate(vectors):
            all_vectors[start + j] = vec

    return all_vectors


# ═══════════════════════════════════════════════════════════════════════════
# § Bulk Insert: Embeddings
# ═══════════════════════════════════════════════════════════════════════════


async def bulk_insert_embeddings(
    embedding_rows: list[dict[str, Any]],
) -> int:
    """Bulk insert embedding records with idempotency.

    Uses INSERT ON CONFLICT (source_id, source_type) DO NOTHING.
    Note: requires a unique constraint on (source_id, source_type) in DB.
    Falls back to skip-on-error if constraint not present.

    Returns:
        Number of rows attempted.
    """
    if not embedding_rows:
        return 0

    with logfire.span("db.bulk_insert_embeddings", count=len(embedding_rows)):
        async with AsyncSessionLocal() as session:
            try:
                stmt = (
                    pg_insert(TextEmbedding)
                    .values(embedding_rows)
                    # If (source_id, source_type) is unique → skip duplicates
                    # Otherwise this falls through to PK uniqueness
                    .on_conflict_do_nothing()
                )
                await session.execute(stmt)
                await session.commit()
                logfire.info(
                    "Embeddings bulk-inserted: {n} rows.",
                    n=len(embedding_rows),
                )
                return len(embedding_rows)
            except Exception as exc:
                await session.rollback()
                logfire.error(
                    "Bulk insert embeddings failed, rolled back: {err}",
                    err=str(exc),
                )
                raise


# ═══════════════════════════════════════════════════════════════════════════
# § Skip already-embedded products
# ═══════════════════════════════════════════════════════════════════════════


async def get_already_embedded_source_ids(source_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Return set of source_ids that already have embeddings (skip in batch)."""
    if not source_ids:
        return set()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(
                f"SELECT DISTINCT source_id FROM {SCHEMA}.text_embeddings "
                f"WHERE source_id = ANY(:ids)"
            ),
            {"ids": source_ids},
        )
        return {row[0] for row in result.fetchall()}


# ═══════════════════════════════════════════════════════════════════════════
# § Main Orchestrator
# ═══════════════════════════════════════════════════════════════════════════


async def run_seed(
    total: int,
    gen_batch_size: int,
    embed_batch_size: int,
    max_embed_concurrency: int,
    skip_hnsw: bool,
    dry_run: bool,
) -> None:
    """Full seeding pipeline with progress tracking and OTel spans."""

    setup_logging()

    console.rule("[bold green]Bulk Product Seeder[/bold green]")
    console.print(
        f"  Total products  : [cyan]{total:,}[/cyan]\n"
        f"  LLM gen batch   : [cyan]{gen_batch_size}[/cyan]\n"
        f"  Embed batch     : [cyan]{embed_batch_size}[/cyan]\n"
        f"  Embed concurrency: [cyan]{max_embed_concurrency}[/cyan]\n"
        f"  HNSW drop/recreate: [cyan]{not skip_hnsw}[/cyan]\n"
        f"  Dry run         : [cyan]{dry_run}[/cyan]\n"
    )

    with logfire.span(
        "seed.pipeline",
        total=total,
        gen_batch_size=gen_batch_size,
        embed_batch_size=embed_batch_size,
    ):
        # ── Step 1: Drop HNSW ─────────────────────────────────────────────
        if not skip_hnsw and not dry_run:
            console.print("[yellow]► Dropping HNSW index before ingest...[/yellow]")
            await drop_hnsw_index()
            console.print("[green]✓ HNSW index dropped.[/green]")

        # ── Step 2: Generate product data via LLM ─────────────────────────
        all_products: list[ProductSeedItem] = []
        num_batches = (total + gen_batch_size - 1) // gen_batch_size
        # Cycle through categories for variety
        num_cats = len(PRODUCT_CATEGORIES)

        console.print(
            f"\n[yellow]► Generating {total:,} products in {num_batches} LLM batches...[/yellow]"
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TaskProgressColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            gen_task = progress.add_task("LLM generation", total=num_batches)

            for batch_idx in range(num_batches):
                remaining = total - len(all_products)
                if remaining <= 0:
                    break
                current_batch_size = min(gen_batch_size, remaining)
                cat_config = PRODUCT_CATEGORIES[batch_idx % num_cats]

                progress.update(
                    gen_task,
                    description=f"LLM gen [{cat_config['category']}]",
                )

                if dry_run:
                    # In dry-run, create stub data without calling LLM
                    stubs = _generate_stub_batch(
                        current_batch_size, cat_config, len(all_products)
                    )
                    all_products.extend(stubs)
                else:
                    batch = await generate_product_batch(
                        batch_size=current_batch_size,
                        category_config=cat_config,
                        start_index=len(all_products),
                    )
                    # Deduplicate SKUs within accumulated list
                    existing_skus = {p.sku for p in all_products}
                    unique_batch = [p for p in batch if p.sku not in existing_skus]
                    all_products.extend(unique_batch)

                    # If LLM returned fewer than requested, warn
                    if len(unique_batch) < current_batch_size:
                        logfire.warn(
                            "LLM returned {got} products, expected {want}.",
                            got=len(unique_batch),
                            want=current_batch_size,
                        )

                progress.advance(gen_task)

        console.print(
            f"[green]✓ Generated {len(all_products):,} unique products.[/green]"
        )

        if not all_products:
            console.print("[red]✗ No products generated. Aborting.[/red]")
            return

        # ── Step 3: TypeAdapter bulk validate ─────────────────────────────
        with logfire.span("validate.bulk", count=len(all_products)):
            validated_products = PRODUCT_LIST_ADAPTER.validate_python(
                [p.model_dump() for p in all_products]
            )
        console.print(
            f"[green]✓ TypeAdapter validation passed: {len(validated_products):,} products.[/green]"
        )

        if dry_run:
            _print_sample(validated_products[:5])
            console.print("[yellow]Dry run complete — no data written to DB.[/yellow]")
            return

        # ── Step 4: Bulk insert products ──────────────────────────────────
        console.print(
            f"\n[yellow]► Bulk inserting {len(validated_products):,} products...[/yellow]"
        )
        product_records = await bulk_insert_products(validated_products)
        console.print(
            f"[green]✓ {len(product_records):,} products in DB (incl. pre-existing).[/green]"
        )

        if not product_records:
            console.print("[red]✗ No product IDs returned. Aborting embedding step.[/red]")
            return

        # ── Step 5: Skip already-embedded products ─────────────────────────
        source_ids = [r["id"] for r in product_records]
        already_embedded = await get_already_embedded_source_ids(source_ids)

        sku_to_id: dict[str, uuid.UUID] = {r["sku"]: r["id"] for r in product_records}
        products_needing_embed = [
            p for p in validated_products
            if sku_to_id.get(p.sku) and sku_to_id[p.sku] not in already_embedded
        ]

        console.print(
            f"\n[yellow]► Embedding {len(products_needing_embed):,} products "
            f"({len(already_embedded):,} already embedded, skipped)...[/yellow]"
        )

        if not products_needing_embed:
            console.print("[green]✓ All products already have embeddings. Skipping.[/green]")
        else:
            # ── Step 6: Async batched embedding ───────────────────────────
            descriptions = [p.description for p in products_needing_embed]

            with logfire.span(
                "embed.pipeline",
                count=len(descriptions),
                batch_size=embed_batch_size,
                concurrency=max_embed_concurrency,
            ):
                vectors = await embed_texts_batched(
                    texts=descriptions,
                    embed_batch_size=embed_batch_size,
                    max_concurrent=max_embed_concurrency,
                )

            console.print(
                f"[green]✓ Generated {len(vectors):,} embedding vectors.[/green]"
            )

            # ── Step 7: Bulk insert embeddings ────────────────────────────
            now = datetime.now(UTC)
            embedding_rows = [
                {
                    "id": uuid.uuid4(),
                    "source_id": sku_to_id[p.sku],
                    "source_type": "product_description",
                    "embedding": vectors[i],
                    "model_name": settings.EMBED_MODEL,
                    "model_version": "v1.0",
                    "created_at": now,
                }
                for i, p in enumerate(products_needing_embed)
                if sku_to_id.get(p.sku) is not None
            ]

            await bulk_insert_embeddings(embedding_rows)
            console.print(
                f"[green]✓ {len(embedding_rows):,} embedding records inserted.[/green]"
            )

        # ── Step 8: Recreate HNSW index ───────────────────────────────────
        if not skip_hnsw:
            console.print(
                "\n[yellow]► Recreating HNSW index (CONCURRENTLY)...[/yellow]"
            )
            await recreate_hnsw_index()
            console.print("[green]✓ HNSW index recreated.[/green]")

        # ── Summary ───────────────────────────────────────────────────────
        _print_summary(
            total_generated=len(validated_products),
            total_in_db=len(product_records),
            total_embedded=len(products_needing_embed),
            already_embedded=len(already_embedded),
        )


# ═══════════════════════════════════════════════════════════════════════════
# § Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _generate_stub_batch(
    batch_size: int,
    cat_config: dict[str, Any],
    offset: int,
) -> list[ProductSeedItem]:
    """Stub product generator for dry-run (no LLM call)."""
    prefix = _category_to_sku_prefix(cat_config["category"])
    stubs = []
    for i in range(batch_size):
        idx = offset + i + 1
        stubs.append(
            ProductSeedItem(
                sku=f"{prefix}-STUB-{idx:05d}",
                name=f"Sản phẩm stub {idx} - {cat_config['category']}",
                description=(
                    f"Đây là sản phẩm stub số {idx} trong danh mục {cat_config['category']}. "
                    f"Sản phẩm này được tạo ra để kiểm tra hiệu suất hệ thống. "
                    f"Chất lượng tốt, phù hợp cho mọi nhu cầu sử dụng hàng ngày."
                ),
                price=float(cat_config["price_range"][0]),
                category=cat_config["category"],
                brand=cat_config["brands"][idx % len(cat_config["brands"])],
                tags=["stub", "test", cat_config["category"].split()[0].lower()],
                in_stock=True,
            )
        )
    return stubs


def _print_sample(products: list[ProductSeedItem]) -> None:
    table = Table(title="Sample Generated Products", show_header=True, header_style="bold cyan")
    table.add_column("SKU", style="yellow", max_width=20)
    table.add_column("Name", max_width=35)
    table.add_column("Price (VND)", justify="right")
    table.add_column("Category", max_width=20)
    for p in products:
        table.add_row(p.sku, p.name, f"{p.price:,.0f}", p.category)
    console.print(table)


def _print_summary(
    total_generated: int,
    total_in_db: int,
    total_embedded: int,
    already_embedded: int,
) -> None:
    table = Table(title="Seed Summary", show_header=True, header_style="bold green")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right", style="bold white")
    table.add_row("Products generated", f"{total_generated:,}")
    table.add_row("Products in DB (incl. pre-existing)", f"{total_in_db:,}")
    table.add_row("New embeddings inserted", f"{total_embedded:,}")
    table.add_row("Already embedded (skipped)", f"{already_embedded:,}")
    console.print(table)
    console.rule("[bold green]Seeding Complete[/bold green]")


# ═══════════════════════════════════════════════════════════════════════════
# § Typer CLI
# ═══════════════════════════════════════════════════════════════════════════


@app.command()
def seed(
    total: int = typer.Option(
        1000,
        "--total",
        "-n",
        help="Total number of products to seed. Start with 1000, scale to 10000/1M.",
    ),
    gen_batch_size: int = typer.Option(
        50,
        "--gen-batch",
        help="Number of products per LLM generation call. Larger = fewer API calls.",
    ),
    embed_batch_size: int = typer.Option(
        32,
        "--embed-batch",
        help="Number of texts per embedding API call.",
    ),
    max_embed_concurrency: int = typer.Option(
        3,
        "--embed-concurrency",
        help="Max concurrent embedding API calls (Semaphore). Lower for Ollama/local.",
    ),
    skip_hnsw: bool = typer.Option(
        False,
        "--skip-hnsw",
        help="Skip HNSW drop/recreate (useful for small incremental seeds < 100 rows).",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Generate and validate data only — no DB writes, no LLM embedding calls.",
    ),
) -> None:
    """Seed the database with LLM-generated, semantically rich product data.

    Examples:

        # Quick test (1000 products, dry run first)
        uv run python scripts/seed_bulk.py seed --total 1000 --dry-run

        # Real seed with 1000 products
        uv run python scripts/seed_bulk.py seed --total 1000

        # Scale to 10k
        uv run python scripts/seed_bulk.py seed --total 10000 --gen-batch 100 --embed-batch 64

        # Skip HNSW management for small incremental updates
        uv run python scripts/seed_bulk.py seed --total 50 --skip-hnsw
    """
    asyncio.run(
        run_seed(
            total=total,
            gen_batch_size=gen_batch_size,
            embed_batch_size=embed_batch_size,
            max_embed_concurrency=max_embed_concurrency,
            skip_hnsw=skip_hnsw,
            dry_run=dry_run,
        )
    )


if __name__ == "__main__":
    app()
