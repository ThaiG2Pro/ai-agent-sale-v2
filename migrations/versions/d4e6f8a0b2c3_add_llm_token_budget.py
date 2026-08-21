"""Add llm_token_budget table (v3-0 P3, T09).

App-side daily token counter per model — the only new infra item P3 allows
(the proposal's zero-cost compliance section). One row per (day UTC, model);
the fallback ladder reads it to proactively skip the premium rung at ~90%
of the free-tier daily cap.

Revision ID: d4e6f8a0b2c3
Revises: c3d5e7f9a1b2
Create Date: 2026-08-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

if TYPE_CHECKING:
    from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "d4e6f8a0b2c3"
down_revision: str | Sequence[str] | None = "c3d5e7f9a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "agent_v1"


def upgrade() -> None:
    op.create_table(
        "llm_token_budget",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.UniqueConstraint("day", "model", name="uq_llm_token_budget_day_model"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("llm_token_budget", schema=SCHEMA)
