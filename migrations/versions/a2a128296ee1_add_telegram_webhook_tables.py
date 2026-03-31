"""add_telegram_webhook_tables

Revision ID: a2a128296ee1
Revises: 210f1c8a8652
Create Date: 2026-03-30 15:26:51.881591

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a2a128296ee1"
down_revision = "210f1c8a8652"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Upgrade schema."""
    # Create telegram_updates table
    op.create_table(
        "telegram_updates",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "update_id",
            sa.BigInteger(),
            nullable=False,
            unique=True,
            comment="Telegram update_id for deduplication",
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "message_type",
            sa.String(50),
            nullable=True,
            comment="Type: message, callback_query, etc.",
        ),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When the update was processed",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "raw_payload",
            sa.JSON(),
            nullable=False,
            comment="Full Telegram update JSON for audit",
        ),
        schema=None,
    )

    # Create indexes
    op.create_index(
        "idx_telegram_updates_chat_id",
        "telegram_updates",
        ["chat_id"],
        unique=False,
    )
    op.create_index(
        "idx_telegram_updates_created_at",
        "telegram_updates",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("idx_telegram_updates_created_at", table_name="telegram_updates")
    op.drop_index("idx_telegram_updates_chat_id", table_name="telegram_updates")
    op.drop_table("telegram_updates")
