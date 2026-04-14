"""add search_text column and update search trigger (P2-M3c)

Revision ID: b2c3d4e5f6a7
Revises: a1c2d3e4f5g6
Create Date: 2026-04-14

Adds memory_entries.search_text (TEXT, nullable) for Jieba CJK-segmented
content. Updates the trigger function to use search_text with fallback to
content, preserving title weight A.

Existing rows: search_text=NULL → trigger uses COALESCE fallback to content.
Run 'python -m src.backend.cli reindex' to populate search_text with CJK
segmentation for all existing entries.
"""

from alembic import op

revision = "b2c3d4e5f6a7"
down_revision = "a1c2d3e4f5g6"
branch_labels = None
depends_on = None

SCHEMA = "neomagi"


def upgrade() -> None:
    """Add search_text column and update trigger to use it."""
    # Add search_text column (nullable, no backfill needed — trigger fallback handles NULL)
    op.execute(
        f"ALTER TABLE {SCHEMA}.memory_entries"
        f" ADD COLUMN IF NOT EXISTS search_text TEXT"
    )

    # Update existing trigger function (Alembic path uses memory_entries_search_trigger)
    # to use COALESCE(search_text, content) for B weight
    op.execute(f"""
        CREATE OR REPLACE FUNCTION {SCHEMA}.memory_entries_search_trigger()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('simple', COALESCE(NEW.title, '')), 'A') ||
                setweight(to_tsvector('simple',
                    COALESCE(NEW.search_text, NEW.content, '')), 'B');
            NEW.updated_at := now();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)


def downgrade() -> None:
    """Revert trigger and drop search_text column."""
    # Restore original trigger function (content only, no search_text)
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

    op.execute(
        f"ALTER TABLE {SCHEMA}.memory_entries DROP COLUMN IF EXISTS search_text"
    )
