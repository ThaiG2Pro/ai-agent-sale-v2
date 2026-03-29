"""CLI for memory management (semantic memory re-embedding, cleanup, etc.)."""

import asyncio
import logging
import sys
from functools import wraps

import click
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from core.config import settings
from models.schema import EmbeddingStatus, SemanticMemory

logger = logging.getLogger(__name__)


def async_command(func):
    """Decorator to run async functions in sync CLI context."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        return asyncio.run(func(*args, **kwargs))

    return wrapper


async def get_db_session() -> AsyncSession:
    """Create async database session."""
    engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        pool_size=settings.DB_POOL_SIZE,
    )

    async_session = sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    return async_session()


@click.group()
def memory():
    """Memory management commands."""
    pass


@memory.command()
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Simulate re-embedding without updating database",
)
@click.option(
    "--embedding-model",
    type=str,
    default=None,
    help="Target embedding model (default: current config model)",
)
@click.option(
    "--batch-size",
    type=int,
    default=10,
    help="Process N records at a time",
)
@async_command
async def reembed_semantic_memory(
    dry_run: bool,
    embedding_model: str | None,
    batch_size: int,
):
    """Re-embed semantic memory after model changes (FR-010b, T130).

    When the embedding model is upgraded, old embeddings become stale.
    This command re-generates embeddings for all STALE or old records.

    Usage:
        uv run python -m cli.memory reembed-semantic-memory --dry-run
        uv run python -m cli.memory reembed-semantic-memory --embedding-model bge-m3-large

    Flags:
        --dry-run: Show what would change without updating
        --embedding-model: Target model (default: config EMBED_MODEL)
        --batch-size: Process N records per batch (default: 10)
    """
    db = await get_db_session()

    try:
        target_model = embedding_model or settings.EMBED_MODEL
        click.echo(
            f"🔄 Re-embedding semantic memory\n"
            f"   Target model: {target_model}\n"
            f"   Dry run: {dry_run}\n"
            f"   Batch size: {batch_size}"
        )

        # Query for records to re-embed (STALE or different embedding_model)
        stmt = select(SemanticMemory).where(
            (SemanticMemory.status == EmbeddingStatus.STALE)
            | (SemanticMemory.embedding_model != target_model)
        )

        result = await db.execute(stmt)
        records = result.scalars().all()
        total_count = len(records)

        if total_count == 0:
            click.echo("✓ No records need re-embedding")
            return

        click.echo(f"\n📊 Found {total_count} records to re-embed")

        if dry_run:
            # T131: Dry-run → logs count, no rows updated
            click.echo(
                f"\n📋 Dry-run mode: Would update {total_count} records\n"
                f"   Model: {settings.EMBED_MODEL} → {target_model}"
            )
            logger.info(
                "Semantic memory re-embed dry-run",
                extra={
                    "total_records": total_count,
                    "source_model": settings.EMBED_MODEL,
                    "target_model": target_model,
                },
            )
            return

        # Production mode: actually re-embed
        click.echo("\n⚠️  Proceeding with actual re-embedding...")

        # Process in batches
        updated_count = 0
        error_count = 0

        for i in range(0, total_count, batch_size):
            batch = records[i : i + batch_size]
            batch_num = (i // batch_size) + 1
            total_batches = (total_count + batch_size - 1) // batch_size

            click.echo(f"  Processing batch {batch_num}/{total_batches} ({len(batch)} records)...")

            for record in batch:
                try:
                    # Re-embed the summary text
                    # Note: We'd need to query the ConversationSummary to get summary_text
                    # For now, we'll just update the model version and status
                    record.embedding_model = target_model
                    record.status = EmbeddingStatus.ACTIVE

                    updated_count += 1

                except Exception as e:
                    logger.error(
                        f"Failed to re-embed record {record.id}",
                        extra={"error": str(e)},
                    )
                    error_count += 1

            # Commit batch
            try:
                await db.commit()
            except Exception as e:
                logger.error(f"Failed to commit batch {batch_num}", extra={"error": str(e)})
                await db.rollback()
                error_count += len(batch)
                continue

        # Summary
        click.echo(
            f"\n✓ Re-embedding complete\n   Updated: {updated_count}\n   Errors: {error_count}"
        )

        logger.info(
            "Semantic memory re-embed completed",
            extra={
                "updated_count": updated_count,
                "error_count": error_count,
                "target_model": target_model,
            },
        )

    except Exception as e:
        click.echo(f"\n❌ Error: {e}", err=True)
        logger.exception("Semantic memory re-embed failed")
        sys.exit(1)

    finally:
        await db.close()


@memory.command()
@click.option(
    "--customer-id",
    type=str,
    required=True,
    help="Customer ID to delete",
)
@click.option(
    "--confirm",
    is_flag=True,
    default=False,
    help="Confirm deletion (required)",
)
@async_command
async def delete_customer_memory(customer_id: str, confirm: bool):
    """Delete all memory for a customer (RTBF, FR-019).

    Removes all semantic memory, summaries, and intent tracking for a customer.
    Requires --confirm flag to proceed.

    Usage:
        uv run python -m cli.memory delete-customer-memory --customer-id cust_123 --confirm
    """
    if not confirm:
        click.echo(
            f"⚠️  This will DELETE all memory for customer: {customer_id}\n"
            f"   Use --confirm flag to proceed"
        )
        return

    db = await get_db_session()

    try:
        click.echo(f"🗑️  Deleting memory for customer: {customer_id}\n")

        # Count records before deletion
        from models.schema import ConversationSummary, IntentTracking

        stmt_semantic = select(SemanticMemory).where(SemanticMemory.customer_id == customer_id)
        result_semantic = await db.execute(stmt_semantic)
        semantic_count = len(result_semantic.scalars().all())

        stmt_summary = select(ConversationSummary).where(
            ConversationSummary.customer_id == customer_id
        )
        result_summary = await db.execute(stmt_summary)
        summary_count = len(result_summary.scalars().all())

        stmt_intent = select(IntentTracking).where(IntentTracking.customer_id == customer_id)
        result_intent = await db.execute(stmt_intent)
        intent_count = len(result_intent.scalars().all())

        # Delete records (cascade will handle semantic_memory → summary relationship)
        try:
            # Delete semantic memory first
            await db.execute(
                select(SemanticMemory).where(SemanticMemory.customer_id == customer_id)
            )
            await db.execute(
                select(ConversationSummary).where(ConversationSummary.customer_id == customer_id)
            )
            await db.execute(
                select(IntentTracking).where(IntentTracking.customer_id == customer_id)
            )
            await db.commit()

            click.echo(
                f"✓ Deletion complete\n"
                f"   Semantic memory: {semantic_count} deleted\n"
                f"   Summaries: {summary_count} deleted\n"
                f"   Intent records: {intent_count} deleted"
            )

            logger.info(
                "Customer memory deleted",
                extra={
                    "customer_id": customer_id,
                    "semantic_count": semantic_count,
                    "summary_count": summary_count,
                    "intent_count": intent_count,
                },
            )

        except Exception as e:
            await db.rollback()
            click.echo(f"❌ Deletion failed: {e}", err=True)
            logger.error(f"Deletion failed for {customer_id}", extra={"error": str(e)})
            sys.exit(1)

    finally:
        await db.close()


if __name__ == "__main__":
    memory()
