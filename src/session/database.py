"""Async database engine and session factory for PostgreSQL persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

import src.memory.models  # noqa: F401 — register memory tables in Base.metadata
from src.constants import DB_SCHEMA
from src.session.models import Base

if TYPE_CHECKING:
    from src.config.settings import DatabaseSettings

logger = structlog.get_logger()


async def create_db_engine(settings: DatabaseSettings) -> AsyncEngine:
    """Create an async SQLAlchemy engine from DatabaseSettings."""
    url = (
        f"postgresql+asyncpg://{settings.user}:{settings.password}"
        f"@{settings.host}:{settings.port}/{settings.name}"
    )
    engine = create_async_engine(
        url,
        pool_size=5,
        max_overflow=10,
        connect_args={"server_settings": {"search_path": f"{settings.schema_}, public"}},
    )
    logger.info("db_engine_created", host=settings.host, database=settings.name)
    return engine


async def ensure_schema(engine: AsyncEngine, schema: str = DB_SCHEMA) -> None:
    """Ensure the target schema exists, then create all tables and search triggers."""
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        await conn.run_sync(Base.metadata.create_all)
        await _add_legacy_columns(conn, schema)
        await _add_memory_entry_columns(conn, schema)
        await _create_search_trigger(conn, schema)
        await _create_skill_tables(conn, schema)

    logger.info("db_schema_ensured", schema=schema)


async def _add_legacy_columns(conn, schema: str) -> None:
    """Add columns for backwards compatibility with older DB schemas."""
    columns = [
        "next_seq INTEGER NOT NULL DEFAULT 0",
        "lock_token VARCHAR(36)",
        "processing_since TIMESTAMPTZ",
        "mode VARCHAR(16) NOT NULL DEFAULT 'chat_safe'",
        "compacted_context TEXT",
        "compaction_metadata JSONB",
        "last_compaction_seq INTEGER",
        "memory_flush_candidates JSONB",
    ]
    for col_def in columns:
        await conn.execute(
            text(f"ALTER TABLE {schema}.sessions ADD COLUMN IF NOT EXISTS {col_def}")
        )


async def _add_memory_entry_columns(conn, schema: str) -> None:
    """Add ADR 0053 provenance columns to memory_entries (idempotent)."""
    columns = [
        "entry_id VARCHAR(36)",
        "source_session_id VARCHAR(256)",
    ]
    for col_def in columns:
        await conn.execute(
            text(f"ALTER TABLE {schema}.memory_entries ADD COLUMN IF NOT EXISTS {col_def}")
        )


async def _create_search_trigger(conn, schema: str) -> None:
    """Create or replace the search vector trigger for memory_entries."""
    await conn.execute(
        text(f"""
        CREATE OR REPLACE FUNCTION {schema}.memory_entries_search_vector_update()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('simple', coalesce(NEW.title, '')), 'A') ||
                setweight(to_tsvector('simple', coalesce(NEW.content, '')), 'B');
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)
    )
    await conn.execute(
        text(
            f"DROP TRIGGER IF EXISTS trg_memory_entries_search_vector"
            f" ON {schema}.memory_entries"
        )
    )
    await conn.execute(
        text(f"""
        CREATE TRIGGER trg_memory_entries_search_vector
        BEFORE INSERT OR UPDATE ON {schema}.memory_entries
        FOR EACH ROW
        EXECUTE FUNCTION {schema}.memory_entries_search_vector_update()
    """)
    )


async def _create_skill_tables(conn, schema: str) -> None:
    """Create skill runtime tables (IF NOT EXISTS) for fresh-DB startup path.

    These tables are normally created by Alembic migration a8b9c0d1e2f3,
    but ensure_schema() must also cover fresh DBs that skip migrations.
    """
    for ddl in _skill_table_ddl(schema):
        await conn.execute(text(ddl))


def _skill_table_ddl(schema: str) -> list[str]:
    """Return idempotent DDL statements for the skill runtime tables."""
    return [
        f"""CREATE TABLE IF NOT EXISTS {schema}.skill_specs (
            id TEXT PRIMARY KEY,
            capability TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            summary TEXT NOT NULL,
            activation TEXT NOT NULL,
            activation_tags JSONB NOT NULL DEFAULT '[]',
            preconditions JSONB NOT NULL DEFAULT '[]',
            delta JSONB NOT NULL DEFAULT '[]',
            tool_preferences JSONB NOT NULL DEFAULT '[]',
            escalation_rules JSONB NOT NULL DEFAULT '[]',
            exchange_policy TEXT NOT NULL DEFAULT 'local_only',
            disabled BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.skill_evidence (
            skill_id TEXT PRIMARY KEY REFERENCES {schema}.skill_specs(id),
            source TEXT NOT NULL,
            success_count INTEGER NOT NULL DEFAULT 0,
            failure_count INTEGER NOT NULL DEFAULT 0,
            last_validated_at TIMESTAMPTZ,
            positive_patterns JSONB NOT NULL DEFAULT '[]',
            negative_patterns JSONB NOT NULL DEFAULT '[]',
            known_breakages JSONB NOT NULL DEFAULT '[]',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )""",
        f"""CREATE TABLE IF NOT EXISTS {schema}.skill_spec_versions (
            governance_version BIGSERIAL PRIMARY KEY,
            skill_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed',
            proposal JSONB NOT NULL,
            eval_result JSONB,
            created_by TEXT NOT NULL DEFAULT 'agent',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            applied_at TIMESTAMPTZ,
            rolled_back_from BIGINT
                REFERENCES {schema}.skill_spec_versions(governance_version)
        )""",
        f"""CREATE INDEX IF NOT EXISTS idx_skill_spec_versions_skill_id
            ON {schema}.skill_spec_versions (skill_id)""",
        f"""CREATE INDEX IF NOT EXISTS idx_skill_spec_versions_status
            ON {schema}.skill_spec_versions (status)""",
    ]


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    """Create an async session factory bound to the engine."""
    return async_sessionmaker(engine, expire_on_commit=False)
