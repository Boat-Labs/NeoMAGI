"""Async database engine and session factory for PostgreSQL persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

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
        connect_args={"server_settings": {"search_path": settings.schema_}},
    )
    logger.info("db_engine_created", host=settings.host, database=settings.name)
    return engine


async def ensure_schema(engine: AsyncEngine, schema: str = "neomagi") -> None:
    """Ensure the target schema exists, then create all tables."""
    async with engine.begin() as conn:
        await conn.execute(
            __import__("sqlalchemy").text(f"CREATE SCHEMA IF NOT EXISTS {schema}")
        )
        await conn.run_sync(Base.metadata.create_all)
    logger.info("db_schema_ensured", schema=schema)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    """Create an async session factory bound to the engine."""
    return async_sessionmaker(engine, expire_on_commit=False)
