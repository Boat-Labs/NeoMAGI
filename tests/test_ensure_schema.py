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
    await _create_legacy_tables(db_engine)
    await ensure_schema(db_engine, DB_SCHEMA)
    await _assert_legacy_columns_backfilled(db_engine)


async def _create_legacy_tables(engine: AsyncEngine) -> None:
    """Create pre-M1.3 legacy table shape."""
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {DB_SCHEMA}.messages CASCADE"))
        await conn.execute(text(f"DROP TABLE IF EXISTS {DB_SCHEMA}.sessions CASCADE"))
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


async def _assert_legacy_columns_backfilled(engine: AsyncEngine) -> None:
    """Assert ensure_schema added all legacy columns."""
    async with engine.begin() as conn:
        result = await conn.execute(text(f"""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = '{DB_SCHEMA}' AND table_name = 'sessions'
        """))
        columns = {row.column_name for row in result}
        expected = {
            "mode", "next_seq", "lock_token", "processing_since",
            "compacted_context", "compaction_metadata",
            "last_compaction_seq", "memory_flush_candidates",
        }
        assert expected.issubset(columns)

        mode_result = await conn.execute(text(
            f"SELECT mode FROM {DB_SCHEMA}.sessions WHERE id = 'legacy-main'"
        ))
        row = mode_result.first()
        assert row is not None
        assert row.mode == "chat_safe"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ensure_schema_adds_memory_entry_provenance_columns(
    db_engine: AsyncEngine,
) -> None:
    """ADR 0053: entry_id and source_session_id columns are idempotently added."""
    await ensure_schema(db_engine, DB_SCHEMA)
    # Second call must be idempotent
    await ensure_schema(db_engine, DB_SCHEMA)

    async with db_engine.begin() as conn:
        result = await conn.execute(text(f"""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = '{DB_SCHEMA}' AND table_name = 'memory_entries'
        """))
        columns = {row.column_name for row in result}
        assert "entry_id" in columns
        assert "source_session_id" in columns


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ensure_schema_adds_search_text_column(
    db_engine: AsyncEngine,
) -> None:
    """P2-M3c: search_text column exists after ensure_schema."""
    await ensure_schema(db_engine, DB_SCHEMA)

    async with db_engine.begin() as conn:
        result = await conn.execute(text(f"""
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = '{DB_SCHEMA}' AND table_name = 'memory_entries'
        """))
        columns = {row.column_name for row in result}
        assert "search_text" in columns


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_text_trigger_fallback(db_engine: AsyncEngine) -> None:
    """P2-M3c: trigger uses search_text when present, falls back to content."""
    await ensure_schema(db_engine, DB_SCHEMA)

    async with db_engine.begin() as conn:
        # Insert with search_text → trigger should use search_text for B weight
        await conn.execute(text(f"""
            INSERT INTO {DB_SCHEMA}.memory_entries
                (scope_key, source_type, title, content, search_text, tags)
            VALUES
                ('main', 'daily_note', 'test', 'original content',
                 'jieba segmented tokens', ARRAY[]::text[])
        """))

        result = await conn.execute(text(f"""
            SELECT search_vector::text AS sv
            FROM {DB_SCHEMA}.memory_entries
            WHERE title = 'test' AND content = 'original content'
        """))
        row = result.first()
        assert row is not None
        # search_vector should contain tokens from search_text, not content
        assert "jieba" in row.sv or "segment" in row.sv or "token" in row.sv

        # Insert without search_text → trigger falls back to content
        await conn.execute(text(f"""
            INSERT INTO {DB_SCHEMA}.memory_entries
                (scope_key, source_type, title, content, tags)
            VALUES
                ('main', 'daily_note', 'fallback', 'fallback content here',
                 ARRAY[]::text[])
        """))

        result2 = await conn.execute(text(f"""
            SELECT search_vector::text AS sv
            FROM {DB_SCHEMA}.memory_entries
            WHERE title = 'fallback'
        """))
        row2 = result2.first()
        assert row2 is not None
        assert "fallback" in row2.sv

        # Cleanup
        await conn.execute(text(
            f"DELETE FROM {DB_SCHEMA}.memory_entries"
            f" WHERE title IN ('test', 'fallback')"
        ))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_alembic_migration_search_text_sql(db_engine: AsyncEngine) -> None:
    """P2-M3c: Execute Alembic migration SQL and verify via Alembic trigger.

    The test DB normally uses the ensure_schema trigger
    (trg_memory_entries_search_vector → memory_entries_search_vector_update).
    To verify the *Alembic* migration path, this test:
    1. Runs the upgrade SQL (column + function)
    2. Binds the Alembic-path trigger (trg_memory_entries_search) so INSERTs
       fire the Alembic function, not the ensure_schema one
    3. Verifies search_text → search_vector via INSERT
    4. Runs downgrade SQL
    5. Restores the ensure_schema trigger for other tests
    """
    schema = DB_SCHEMA

    async with db_engine.begin() as conn:
        # ── Upgrade ──
        await conn.execute(text(
            f"ALTER TABLE {schema}.memory_entries"
            f" ADD COLUMN IF NOT EXISTS search_text TEXT"
        ))
        await conn.execute(text(f"""
            CREATE OR REPLACE FUNCTION {schema}.memory_entries_search_trigger()
            RETURNS trigger AS $$
            BEGIN
                NEW.search_vector :=
                    setweight(to_tsvector('simple', COALESCE(NEW.title, '')), 'A')
                    || setweight(to_tsvector('simple',
                        COALESCE(NEW.search_text, NEW.content, '')), 'B');
                NEW.updated_at := now();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """))

        # Verify column exists
        result = await conn.execute(text(
            f"SELECT column_name FROM information_schema.columns"
            f" WHERE table_schema = '{schema}'"
            f" AND table_name = 'memory_entries'"
            f" AND column_name = 'search_text'"
        ))
        assert result.first() is not None, "search_text column not created"

        # ── Bind the Alembic-path trigger so the INSERT fires it ──
        # Drop the ensure_schema trigger temporarily
        await conn.execute(text(
            f"DROP TRIGGER IF EXISTS trg_memory_entries_search_vector"
            f" ON {schema}.memory_entries"
        ))
        # Create the Alembic-path trigger
        await conn.execute(text(f"""
            CREATE TRIGGER trg_memory_entries_search
            BEFORE INSERT OR UPDATE ON {schema}.memory_entries
            FOR EACH ROW
            EXECUTE FUNCTION {schema}.memory_entries_search_trigger()
        """))

        # Verify: INSERT fires Alembic function (uses search_text, not content)
        await conn.execute(text(f"""
            INSERT INTO {schema}.memory_entries
                (scope_key, source_type, title, content, search_text, tags)
            VALUES
                ('main', 'daily_note', 'mig_test', 'raw content only',
                 'segmented tokens here', ARRAY[]::text[])
        """))
        result = await conn.execute(text(
            f"SELECT search_vector::text AS sv"
            f" FROM {schema}.memory_entries WHERE title = 'mig_test'"
        ))
        row = result.first()
        assert row is not None
        # Must contain tokens from search_text, NOT from content
        assert "segment" in row.sv or "token" in row.sv
        assert "raw" not in row.sv, (
            "search_vector contains content tokens — "
            "Alembic trigger not active"
        )

        # ── Downgrade ──
        await conn.execute(text(
            f"DROP TRIGGER IF EXISTS trg_memory_entries_search"
            f" ON {schema}.memory_entries"
        ))
        await conn.execute(text(f"""
            CREATE OR REPLACE FUNCTION {schema}.memory_entries_search_trigger()
            RETURNS trigger AS $$
            BEGIN
                NEW.search_vector :=
                    setweight(to_tsvector('simple', COALESCE(NEW.title, '')), 'A')
                    || setweight(to_tsvector('simple',
                        COALESCE(NEW.content, '')), 'B');
                NEW.updated_at := now();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """))
        await conn.execute(text(
            f"ALTER TABLE {schema}.memory_entries"
            f" DROP COLUMN IF EXISTS search_text"
        ))

        # Verify column gone
        result = await conn.execute(text(
            f"SELECT column_name FROM information_schema.columns"
            f" WHERE table_schema = '{schema}'"
            f" AND table_name = 'memory_entries'"
            f" AND column_name = 'search_text'"
        ))
        assert result.first() is None, "search_text column not dropped"

        # Cleanup test row
        await conn.execute(text(
            f"DELETE FROM {schema}.memory_entries WHERE title = 'mig_test'"
        ))

    # Restore ensure_schema state (column + ensure_schema trigger) for other tests
    await ensure_schema(db_engine, DB_SCHEMA)
