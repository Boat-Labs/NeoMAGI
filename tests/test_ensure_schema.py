"""Tests for ensure_schema search trigger DDL.

Covers: trigger creation is idempotent (can be called multiple times),
and search_vector is auto-populated on INSERT.

Marked as integration — requires a live PostgreSQL instance.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.constants import DB_SCHEMA
from src.session.database import ensure_schema


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ensure_schema_creates_trigger(db_engine: AsyncEngine) -> None:
    """Trigger is idempotent and search_vector is populated on INSERT."""
    # Run ensure_schema twice — second call must not error (idempotent)
    await ensure_schema(db_engine, DB_SCHEMA)
    await ensure_schema(db_engine, DB_SCHEMA)

    # Insert a row into memory_entries and verify search_vector is populated
    async with db_engine.begin() as conn:
        await conn.execute(text(f"""
            INSERT INTO {DB_SCHEMA}.memory_entries
                (scope_key, source_type, title, content, tags)
            VALUES
                ('main', 'daily_note', 'test title', 'hello world content', ARRAY[]::text[])
        """))

        result = await conn.execute(text(f"""
            SELECT search_vector IS NOT NULL AS has_vector
            FROM {DB_SCHEMA}.memory_entries
            WHERE title = 'test title'
        """))
        row = result.first()
        assert row is not None
        assert row.has_vector is True

        # Cleanup
        await conn.execute(text(
            f"DELETE FROM {DB_SCHEMA}.memory_entries WHERE title = 'test title'"
        ))
