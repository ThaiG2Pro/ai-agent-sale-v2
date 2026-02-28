"""add_unaccent_vietnamese_fts

Adds:
- unaccent PostgreSQL extension (diacritic stripping)
- agent_v1.immutable_unaccent() — IMMUTABLE wrapper required for
  generated columns and function indexes (unaccent() itself is STABLE)
- content_tsvector GENERATED ALWAYS AS stored column on products
  with setweight A (name) + B (description) + unaccent normalization
- GIN index on the stored column (faster than expression index)
- Drops the old expression-based idx_products_fts

Why: Vietnamese users search without diacritics ("dien thoai" → "điện thoại").
The old 'simple' index without unaccent would miss these queries entirely.
setweight A/B ensures product names rank above descriptions.

Revision ID: f8a2c1d3e5b7
Revises: 05a8b68c724f
Create Date: 2026-02-28
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "f8a2c1d3e5b7"
down_revision: str | Sequence[str] | None = "05a8b68c724f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Enable unaccent extension (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent;")

    # 2. Create IMMUTABLE wrapper for unaccent().
    #    unaccent() is STABLE (depends on dictionary files) so PostgreSQL
    #    refuses to use it in GENERATED columns or function indexes.
    #    Wrapping it in an IMMUTABLE SQL function is the standard workaround.
    op.execute("""
        CREATE OR REPLACE FUNCTION agent_v1.immutable_unaccent(text)
        RETURNS text
        LANGUAGE sql
        IMMUTABLE PARALLEL SAFE STRICT
        AS $$ SELECT public.unaccent('unaccent', $1) $$;
    """)

    # 3. Add stored generated tsvector column:
    #    - 'simple' config: no stemming (correct for Vietnamese isolating morphology)
    #    - unaccent: strips diacritics so "dien thoai" matches "điện thoại"
    #    - setweight A (name) outranks B (description) in ts_rank
    op.execute("""
        ALTER TABLE agent_v1.products
        ADD COLUMN content_tsvector tsvector
        GENERATED ALWAYS AS (
            setweight(
                to_tsvector('simple', agent_v1.immutable_unaccent(coalesce(name, ''))),
                'A'
            ) ||
            setweight(
                to_tsvector(
                    'simple',
                    agent_v1.immutable_unaccent(coalesce(description, ''))
                ),
                'B'
            )
        ) STORED;
    """)

    # 4. GIN index on the stored column — index is built once, not per-query
    op.execute("""
        CREATE INDEX idx_products_content_tsvector
        ON agent_v1.products
        USING GIN(content_tsvector);
    """)

    # 5. Drop the old expression-based GIN index (superseded)
    op.execute("DROP INDEX IF EXISTS agent_v1.idx_products_fts;")


def downgrade() -> None:
    # Restore old expression-based index
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_products_fts
        ON agent_v1.products
        USING gin(
            to_tsvector('simple', COALESCE(name,'') || ' ' || COALESCE(description,''))
        );
    """)
    op.execute("DROP INDEX IF EXISTS agent_v1.idx_products_content_tsvector;")
    op.execute("ALTER TABLE agent_v1.products DROP COLUMN IF EXISTS content_tsvector;")
    op.execute("DROP FUNCTION IF EXISTS agent_v1.immutable_unaccent(text);")
