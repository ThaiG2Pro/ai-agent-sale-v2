"""add_episodic_events

Revision ID: b7e4d2a91c05
Revises: a2a128296ee1
Create Date: 2026-07-31

WP-V2-4: append-only episodic memory table (R-DB-001: rollback via downgrade).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b7e4d2a91c05"
down_revision: str | Sequence[str] | None = "a2a128296ee1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "episodic_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("customer_id", sa.String(length=100), nullable=False),
        sa.Column("thread_id", sa.String(length=100), nullable=False),
        sa.Column("user_message", sa.Text(), nullable=False),
        sa.Column("response_summary", sa.Text(), nullable=True),
        sa.Column("intent", sa.String(length=50), nullable=True),
        sa.Column("products", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema="agent_v1",
    )
    op.create_index(
        op.f("ix_agent_v1_episodic_events_customer_id"),
        "episodic_events",
        ["customer_id"],
        unique=False,
        schema="agent_v1",
    )
    op.create_index(
        op.f("ix_agent_v1_episodic_events_created_at"),
        "episodic_events",
        ["created_at"],
        unique=False,
        schema="agent_v1",
    )
    op.create_index(
        "idx_episodic_events_customer_created",
        "episodic_events",
        ["customer_id", "created_at"],
        unique=False,
        schema="agent_v1",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "idx_episodic_events_customer_created",
        table_name="episodic_events",
        schema="agent_v1",
    )
    op.drop_index(
        op.f("ix_agent_v1_episodic_events_created_at"),
        table_name="episodic_events",
        schema="agent_v1",
    )
    op.drop_index(
        op.f("ix_agent_v1_episodic_events_customer_id"),
        table_name="episodic_events",
        schema="agent_v1",
    )
    op.drop_table("episodic_events", schema="agent_v1")
