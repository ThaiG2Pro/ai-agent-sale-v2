"""Why this exists: CLI for managing the RAG pipeline (Ingestion & Search).
What it does: Provides a command-line interface to ingest and search products.
"""

import argparse
import asyncio
import json
import sys

from services.database import AsyncSessionLocal
from services.rag import ingest_product_text, search_products


async def handle_ingest(args):
    """Handles product ingestion from CLI."""
    async with AsyncSessionLocal() as db:
        try:
            product_id = await ingest_product_text(
                db=db,
                name=args.name,
                sku=args.sku,
                description=args.description,
                price=args.price,
                metadata=json.loads(args.metadata) if args.metadata else None,
            )
            print(f"✓ Ingested successfully. Product ID: {product_id}")
        except Exception as e:
            print(f"✗ Ingestion failed: {e}", file=sys.stderr)
            sys.exit(1)


async def handle_search(args):
    """Handles vector search from CLI."""
    async with AsyncSessionLocal() as db:
        try:
            results = await search_products(db=db, query=args.query, top_k=args.top_k)
            print(json.dumps(results, indent=2))
        except Exception as e:
            print(f"✗ Search failed: {e}", file=sys.stderr)
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="RAG Administration CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Ingest command
    ingest_parser = subparsers.add_parser("ingest", help="Ingest a product")
    ingest_parser.add_argument("--name", required=True, help="Product name")
    ingest_parser.add_argument("--sku", required=True, help="Product SKU")
    ingest_parser.add_argument(
        "--description", required=True, help="Product description for embedding"
    )
    ingest_parser.add_argument("--price", type=float, default=0.0, help="Product price")
    ingest_parser.add_argument("--metadata", help="JSON string of metadata")

    # Search command
    search_parser = subparsers.add_parser("search", help="Perform a vector search")
    search_parser.add_argument("query", help="Search query string")
    search_parser.add_argument(
        "--top-k", type=int, default=5, help="Number of results to return"
    )

    args = parser.parse_args()

    if args.command == "ingest":
        asyncio.run(handle_ingest(args))
    elif args.command == "search":
        asyncio.run(handle_search(args))


if __name__ == "__main__":
    main()
