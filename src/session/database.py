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

        # Legacy compatibility: older DBs may have sessions table without
        # M1.3/M1.5/M2 columns because create_all does not ALTER existing tables.
        # Keep startup self-healing for additive columns.
        await conn.execute(text(
            f"ALTER TABLE {schema}.sessions "
            "ADD COLUMN IF NOT EXISTS next_seq INTEGER NOT NULL DEFAULT 0"
        ))
        await conn.execute(text(
            f"ALTER TABLE {schema}.sessions "
            "ADD COLUMN IF NOT EXISTS lock_token VARCHAR(36)"
        ))
        await conn.execute(text(
            f"ALTER TABLE {schema}.sessions "
            "ADD COLUMN IF NOT EXISTS processing_since TIMESTAMPTZ"
        ))
        await conn.execute(text(
            f"ALTER TABLE {schema}.sessions "
            "ADD COLUMN IF NOT EXISTS mode VARCHAR(16) NOT NULL DEFAULT 'chat_safe'"
        ))
        await conn.execute(text(
            f"ALTER TABLE {schema}.sessions "
            "ADD COLUMN IF NOT EXISTS compacted_context TEXT"
        ))
        await conn.execute(text(
            f"ALTER TABLE {schema}.sessions "
            "ADD COLUMN IF NOT EXISTS compaction_metadata JSONB"
        ))
        await conn.execute(text(
            f"ALTER TABLE {schema}.sessions "
            "ADD COLUMN IF NOT EXISTS last_compaction_seq INTEGER"
        ))
        await conn.execute(text(
            f"ALTER TABLE {schema}.sessions "
            "ADD COLUMN IF NOT EXISTS memory_flush_candidates JSONB"
        ))

        # Search vector trigger: auto-populate search_vector on INSERT/UPDATE.
        # Three separate execute() calls to avoid asyncpg multi-statement issues.
        await conn.execute(text(f"""
            CREATE OR REPLACE FUNCTION {schema}.memory_entries_search_vector_update()
            RETURNS trigger AS $$
            BEGIN
                NEW.search_vector :=
                    setweight(to_tsvector('simple', coalesce(NEW.title, '')), 'A') ||
                    setweight(to_tsvector('simple', coalesce(NEW.content, '')), 'B');
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """))

        await conn.execute(text(
            f"DROP TRIGGER IF EXISTS trg_memory_entries_search_vector"
            f" ON {schema}.memory_entries"
        ))

        await conn.execute(text(f"""
            CREATE TRIGGER trg_memory_entries_search_vector
            BEFORE INSERT OR UPDATE ON {schema}.memory_entries
            FOR EACH ROW
            EXECUTE FUNCTION {schema}.memory_entries_search_vector_update()
        """))

    logger.info("db_schema_ensured", schema=schema)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    """Create an async session factory bound to the engine."""
    return async_sessionmaker(engine, expire_on_commit=False)
