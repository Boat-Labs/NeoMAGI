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


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ensure_schema_backfills_legacy_sessions_columns(
    db_engine: AsyncEngine,
) -> None:
    """Legacy sessions table should be upgraded with missing additive columns."""
    async with db_engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {DB_SCHEMA}.messages CASCADE"))
        await conn.execute(text(f"DROP TABLE IF EXISTS {DB_SCHEMA}.sessions CASCADE"))

        # Simulate pre-M1.3 legacy table shape.
        await conn.execute(text(f"""
            CREATE TABLE {DB_SCHEMA}.sessions (
                id VARCHAR(128) PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))
        await conn.execute(text(
            f"INSERT INTO {DB_SCHEMA}.sessions (id) VALUES ('legacy-main')"
        ))

        await conn.execute(text(f"""
            CREATE TABLE {DB_SCHEMA}.messages (
                id SERIAL PRIMARY KEY,
                session_id VARCHAR(128) NOT NULL REFERENCES {DB_SCHEMA}.sessions(id),
                seq INTEGER NOT NULL,
                role VARCHAR(16) NOT NULL,
                content TEXT NOT NULL,
                tool_calls JSONB,
                tool_call_id VARCHAR(64),
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """))

    await ensure_schema(db_engine, DB_SCHEMA)

    async with db_engine.begin() as conn:
        result = await conn.execute(text(f"""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = '{DB_SCHEMA}' AND table_name = 'sessions'
        """))
        columns = {row.column_name for row in result}

        expected = {
            "mode",
            "next_seq",
            "lock_token",
            "processing_since",
            "compacted_context",
            "compaction_metadata",
            "last_compaction_seq",
            "memory_flush_candidates",
        }
        assert expected.issubset(columns)

        # Existing rows should be readable with mode defaulted.
        mode_result = await conn.execute(text(
            f"SELECT mode FROM {DB_SCHEMA}.sessions WHERE id = 'legacy-main'"
        ))
        row = mode_result.first()
        assert row is not None
        assert row.mode == "chat_safe"
