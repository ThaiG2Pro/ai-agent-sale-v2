"""add_gin_index_products_fts

Revision ID: e9f1c3add123
Revises: dcd5e99fdf41
Create Date: 2026-02-26 14:00:00.000000

"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e9f1c3add123"
down_revision: str | Sequence[str] | None = "dcd5e99fdf41"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema: create GIN index for products FTS."""
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_products_fts
        ON agent_v1.products
        USING gin(
            to_tsvector('simple',
                COALESCE(name,'') || ' ' || COALESCE(description,''))
        );
        """
    )


def downgrade() -> None:
    """Downgrade schema: drop GIN index if exists."""
    op.execute(
        """
        DROP INDEX IF EXISTS agent_v1.idx_products_fts;
        """
    )
