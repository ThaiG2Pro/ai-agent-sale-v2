"""Add draft-order lifecycle columns (v3-0 P2, T05/T07/T13).

- orders.supersedes_id: nullable self-FK — links a replacement draft to the
  draft it supersedes (change-of-mind audit chain, no row deletion).
- hitl_metadata.handoff_package: JSONB — 4-part handoff package built at
  pause time (summary+reason, draft snapshot, intent log, suggested actions).

Revision ID: c3d5e7f9a1b2
Revises: b7e4d2a91c05
Create Date: 2026-08-14
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "c3d5e7f9a1b2"
down_revision: str | Sequence[str] | None = "b7e4d2a91c05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "agent_v1"


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("supersedes_id", UUID(as_uuid=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_foreign_key(
        "fk_orders_supersedes_id",
        "orders",
        "orders",
        ["supersedes_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
    )
    op.add_column(
        "hitl_metadata",
        sa.Column("handoff_package", JSONB, nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("hitl_metadata", "handoff_package", schema=SCHEMA)
    op.drop_constraint("fk_orders_supersedes_id", "orders", schema=SCHEMA)
    op.drop_column("orders", "supersedes_id", schema=SCHEMA)
