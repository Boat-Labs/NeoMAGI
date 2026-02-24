"""create memory_entries table with tsvector search index

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-02-24

ParadeDB pg_search spike: unavailable in target PG instance.
Fallback: PostgreSQL native tsvector + GIN index.
pg_search BM25 can be added as a future migration when available.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR

# revision identifiers, used by Alembic.
revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None

SCHEMA = "neomagi"


def upgrade() -> None:
    """Create memory_entries table with search index."""
    op.create_table(
        "memory_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "scope_key",
            sa.String(length=128),
            nullable=False,
            server_default="main",
        ),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("source_path", sa.String(length=256), nullable=True),
        sa.Column("source_date", sa.Date(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("tags", ARRAY(sa.Text()), server_default="{}"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("search_vector", TSVECTOR(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        schema=SCHEMA,
    )

    # Scope-aware filtering index (ADR 0034)
    op.create_index(
        "idx_memory_entries_scope",
        "memory_entries",
        ["scope_key"],
        schema=SCHEMA,
    )

    # GIN index on tsvector for full-text search
    op.create_index(
        "idx_memory_entries_search",
        "memory_entries",
        ["search_vector"],
        schema=SCHEMA,
        postgresql_using="gin",
    )

    # Trigger to auto-update search_vector on INSERT/UPDATE
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.memory_entries_search_trigger()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('simple', COALESCE(NEW.title, '')), 'A') ||
                setweight(to_tsvector('simple', COALESCE(NEW.content, '')), 'B');
            NEW.updated_at := now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)

    op.execute(f"""
        CREATE TRIGGER trg_memory_entries_search
        BEFORE INSERT OR UPDATE ON {SCHEMA}.memory_entries
        FOR EACH ROW
        EXECUTE FUNCTION {SCHEMA}.memory_entries_search_trigger();
    """)


def downgrade() -> None:
    """Drop memory_entries table and related objects."""
    op.execute(
        f"DROP TRIGGER IF EXISTS trg_memory_entries_search ON {SCHEMA}.memory_entries"
    )
    op.execute(
        f"DROP FUNCTION IF EXISTS {SCHEMA}.memory_entries_search_trigger()"
    )
    op.drop_index(
        "idx_memory_entries_search",
        table_name="memory_entries",
        schema=SCHEMA,
    )
    op.drop_index(
        "idx_memory_entries_scope",
        table_name="memory_entries",
        schema=SCHEMA,
    )
    op.drop_table("memory_entries", schema=SCHEMA)
