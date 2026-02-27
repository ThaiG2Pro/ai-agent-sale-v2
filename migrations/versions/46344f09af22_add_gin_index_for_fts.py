"""add_gin_index_for_fts

Revision ID: 46344f09af22
Revises: dcd5e99fdf41
Create Date: 2026-02-26 21:19:31.010480

"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "46344f09af22"
down_revision: str | Sequence[str] | None = "e9f1c3add123"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Auto-detected: add citations column to semantic_cache
    op.add_column(
        "semantic_cache",
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="agent_v1",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("semantic_cache", "citations", schema="agent_v1")
