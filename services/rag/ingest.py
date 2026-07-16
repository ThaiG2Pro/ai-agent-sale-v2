"""Product ingestion: create products and their embeddings.

Phase 1 additions (Metadata enrichment):
- extract_keywords_structured(): LiteLLM-based keyword extraction
- enrich_metadata_async(): Extract specs, category, intent, summary
- validate_metadata_vs_source(): Critic pattern for hallucination detection
"""

from __future__ import annotations

import time
from typing import Any

import logfire
from sqlalchemy import select
from uuid_utils import uuid7

from core.config import settings
from models.schema import Product, TextEmbedding
from services.ai import AIGateway, KeywordExtraction, ProductMetadata
from services.semantic_cache import invalidate_cache


async def extract_keywords_structured(
    text: str,
    product_name: str,
    count: int = 5,
) -> list[str]:
    """
    Why this exists: Extract keywords for hybrid search (Phase 1).
    What it does: Uses LiteLLM + Pydantic to extract quality keywords.
    Why not simple regex: Pydantic ensures min/max length constraints.

    Returns: List of 3-10 keywords, or empty list on error.
    """
    start_time = time.perf_counter()
    messages = [
        {
            "role": "system",
            "content": (
                "You are a keyword extraction expert for product search. "
                "Extract HIGH-QUALITY keywords that will help customers "
                "FIND this product. "
                "Focus on:\n"
                "- Product name/model\n"
                "- Key technical specs\n"
                "- Common search terms\n"
                "Do NOT include:\n"
                "- Generic words (product, item, thing)\n"
                "- Words already in product name\n"
                "Respond in JSON only."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Product: {product_name}\n\nDescription:\n{text[:500]}"  # Limit to 500 chars
            ),
        },
    ]

    try:
        from services.ai import ai_router

        response = await ai_router.acompletion(
            model="light-chat",  # qwen3:0.6b — cheap extraction task
            messages=messages,
            response_format=KeywordExtraction,
            temperature=0,  # Consistency
            timeout=45,  # Hard cap — prevents 900s+ hangs on Ollama
        )
        latency = time.perf_counter() - start_time
        content = response.choices[0].message.content
        extracted = KeywordExtraction.model_validate_json(content)
        logfire.info(
            "Keywords extracted: count={c}, latency={l:.3f}s",
            c=len(extracted.keywords),
            l=latency,
        )
        return extracted.keywords[:count]
    except Exception as exc:
        latency = time.perf_counter() - start_time
        logfire.warn(
            "Keyword extraction failed: {err} ({latency:.3f}s)",
            err=str(exc),
            latency=latency,
        )
        return []


async def enrich_metadata_async(
    text: str,
    product_name: str,
    sku: str,
) -> ProductMetadata:
    """
    Why this exists: Async metadata enrichment (Phase 1).
    What it does: Extracts specs, category, intent, summary via LLM.
    Why async: Non-blocking LLM calls keep event loop responsive.

    Returns: Fully populated ProductMetadata or minimal fallback on error.
    """
    start_time = time.perf_counter()
    messages = [
        {
            "role": "system",
            "content": (
                "You are a product metadata enrichment expert. "
                "Analyze the product and extract structured metadata.\n"
                "Rules:\n"
                "1. technical_specs: Extract ONLY specs mentioned in the text\n"
                "2. keywords: 5-10 words users would search for\n"
                "3. seo_summary: Short (< 100 char) marketing-friendly description\n"
                "4. category: Product type (e.g., Electronics, Appliances, "
                "Tools)\n"
                "5. intent: commercial (B2B) or consumer (B2C) - infer from "
                "description\n"
                "DO NOT HALLUCINATE specs not mentioned in the description.\n"
                "Respond in JSON only."
            ),
        },
        {
            "role": "user",
            "content": (f"SKU: {sku}\nName: {product_name}\n\nDescription:\n{text}"),
        },
    ]

    try:
        from services.ai import ai_router

        response = await ai_router.acompletion(
            model="economy-chat",
            messages=messages,
            response_format=ProductMetadata,
            temperature=0,  # Consistency
        )
        latency = time.perf_counter() - start_time
        content = response.choices[0].message.content
        enriched = ProductMetadata.model_validate_json(content)
        logfire.info(
            "Metadata enriched: specs_count={sc}, keywords={kc}, category={cat}, latency={l:.3f}s",
            sc=len(enriched.technical_specs),
            kc=len(enriched.keywords),
            cat=enriched.category,
            l=latency,
        )
        return enriched
    except Exception as exc:
        latency = time.perf_counter() - start_time
        logfire.warn(
            "Metadata enrichment failed: {err} ({latency:.3f}s), using minimal fallback",
            err=str(exc),
            latency=latency,
        )
        return ProductMetadata.minimal(sku, product_name)


async def validate_metadata_vs_source(
    original_text: str,
    extracted_metadata: ProductMetadata,
) -> bool:
    """
    Why this exists: Critic pattern for hallucination detection (Phase 1).
    What it does: Validates extracted metadata against original text.
    Why important: Prevents storing false specs or invented categories.

    Validation rules (language-agnostic):
    - PASS based on spec values alone (40% of spec values found in text)
    - Keywords are NOT checked — LLM may return English keywords for Vietnamese text,
      causing false negatives. Keywords serve search, not validation.
    - Spec key names are NOT checked — LLM may translate keys to English
      even when description is in Vietnamese (e.g. "chip" vs "bộ xử lý").

    Returns: True if metadata is valid, False if likely hallucinated.
    """
    start_time = time.perf_counter()

    text_lower = original_text.lower()

    # Keywords: logged for diagnostics but NOT used for validity decision.
    # Reason: qwen3-1.7b returns English keywords for Vietnamese text, causing
    # false negatives. Spec values are a better hallucination signal.
    keywords = extracted_metadata.keywords or []
    if keywords:
        found_kw = sum(1 for kw in keywords if kw.lower() in text_lower)
        keywords_valid = found_kw >= len(keywords) * 0.5
    else:
        keywords_valid = False

    # Check spec VALUES appear in text (not keys — may be in different language)
    specs = extracted_metadata.technical_specs or {}
    if specs:
        found_vals = sum(1 for v in specs.values() if str(v).lower() in text_lower)
        specs_valid = found_vals >= len(specs) * 0.4  # 40% spec values found
    else:
        specs_valid = True  # No specs is OK for simple products

    latency = time.perf_counter() - start_time
    # Validity = specs only; keywords diagnostic only
    is_valid = specs_valid

    logfire.info(
        "Metadata validation: valid={v}, keywords_valid={kv}(diagnostic), "
        "specs_valid={sv}, latency={l:.3f}s",
        v=is_valid,
        kv=keywords_valid,
        sv=specs_valid,
        l=latency,
    )

    return is_valid


async def ingest_product_text(
    db,
    name: str,
    sku: str,
    description: str,
    price: float = 0.0,
    metadata: dict[str, Any] | None = None,
) -> str:
    """
    Why this exists: Populates the system with searchable product knowledge.
    What it does: Creates a Product record and stores its embedding.
    Phase 1: Enriches metadata with specs, category, intent, keywords.
    Uses UUIDv7 for optimal ordering and client-side generation.
    Skips (returns existing ID) if SKU already exists — idempotent.
    """
    # Skip if already ingested — safe for bulk re-runs
    existing = await db.execute(select(Product).where(Product.sku == sku))
    existing_product = existing.scalar_one_or_none()
    if existing_product:
        logfire.info(
            "Product already exists, skipping: {sku}",
            sku=sku,
        )
        return str(existing_product.id)

    product_id = uuid7()

    # 1. Create Product record
    product = Product(
        id=product_id,
        name=name,
        sku=sku,
        description=description,
        price=price,
        metadata_=metadata or {},
    )
    db.add(product)
    await db.flush()

    # 2. Embed — sequential before enrich to avoid OOM from concurrent model loads
    logfire.info("Generating embedding: {sku}", sku=sku)
    price_line = f"Giá: {price:,.0f} VND" if price else ""
    embed_text = f"{name}\n{price_line}\n{description}".strip()
    embeddings_result = await AIGateway.embed(input_text=embed_text, model="economy-embedding")
    vector = embeddings_result[0]

    # 3. Enrich metadata — after embed so Ollama only loads one model at a time
    logfire.info("Enriching metadata: {sku}", sku=sku)
    enriched_metadata = await enrich_metadata_async(description, name, sku)

    # 4. Validate metadata against original text (Critic pattern)
    is_metadata_valid = await validate_metadata_vs_source(description, enriched_metadata)

    if is_metadata_valid:
        product.metadata_ = enriched_metadata.model_dump()
        logfire.info("Metadata validation passed, using enriched metadata")
    else:
        product.metadata_ = ProductMetadata.minimal(sku, name).model_dump()
        logfire.warn("Metadata validation failed, falling back to minimal metadata")

    # 5. Extract keywords — fallback from enriched if LLM fails
    keywords = await extract_keywords_structured(description, name)
    if not keywords:
        keywords = enriched_metadata.keywords if is_metadata_valid else []

    # 6. Create TextEmbedding record with governance fields
    embedding_record = TextEmbedding(
        id=uuid7(),
        source_id=product_id,
        source_type="product_description",
        embedding=vector,
        model_name=settings.EMBED_MODEL,
        model_version="v1.0",
        keywords=keywords,
    )
    db.add(embedding_record)
    await db.commit()

    # Product catalog changed — cached answers may reference stale price/stock.
    await invalidate_cache(db)

    logfire.info(
        "Product ingested: id={pid}, sku={sku}, metadata_enriched={valid}",
        pid=str(product_id)[:8],
        sku=sku,
        valid=is_metadata_valid,
    )

    return str(product_id)
