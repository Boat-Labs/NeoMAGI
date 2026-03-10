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
        await _create_search_trigger(conn, schema)

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


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    """Create an async session factory bound to the engine."""
    return async_sessionmaker(engine, expire_on_commit=False)
