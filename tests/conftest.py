"""Shared pytest fixtures for NeoMAGI tests.

Provides containerized PostgreSQL for integration tests via two modes:
1. TEST_DATABASE_* env vars present → connect to external PG (CI scenario)
2. Otherwise → testcontainers auto-starts a temporary PG container (local dev)

Safety: refuses to run against any database whose name doesn't contain '_test'.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.constants import DB_SCHEMA
from src.session.models import Base


def _validate_test_db_name(name: str) -> None:
    """Safety: refuse to truncate a database whose name doesn't contain '_test'."""
    if "_test" not in name.lower():
        raise RuntimeError(
            f"Refusing to run tests against database '{name}': "
            "name must contain '_test' to prevent accidental data loss. "
            "Set TEST_DATABASE_NAME to a test-specific database."
        )


def _build_pg_url_from_env() -> str | None:
    """Build async PG URL from TEST_DATABASE_* env vars, or return None."""
    host = os.getenv("TEST_DATABASE_HOST")
    if host is None:
        return None
    port = os.getenv("TEST_DATABASE_PORT", "5432")
    user = os.getenv("TEST_DATABASE_USER", "postgres")
    password = os.getenv("TEST_DATABASE_PASSWORD", "")
    name = os.getenv("TEST_DATABASE_NAME", "neomagi_test")
    _validate_test_db_name(name)
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}"


@pytest.fixture(scope="session")
def _pg_container():
    """Manage testcontainers PostgreSQL lifecycle.

    Yields (url, container) where container is None if using external PG.
    Container.stop() runs in teardown — guaranteed by pytest fixture protocol.
    """
    url = _build_pg_url_from_env()
    if url is not None:
        yield url, None
        return

    from testcontainers.postgres import PostgresContainer

    container = PostgresContainer("postgres:16", dbname="neomagi_test")
    container.start()

    host = container.get_container_host_ip()
    port = container.get_exposed_port(5432)
    user = container.username
    password = container.password
    dbname = container.dbname
    _validate_test_db_name(dbname)

    url = f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{dbname}"

    yield url, container

    container.stop()


@pytest.fixture(scope="session")
def pg_url(_pg_container) -> str:
    """Provide an async PostgreSQL URL for integration tests."""
    url, _ = _pg_container
    return url


@pytest_asyncio.fixture(scope="session")
async def db_engine(pg_url: str):
    """Create async engine, set up schema + tables. Tear down after session."""
    engine = create_async_engine(pg_url, echo=False)

    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}"))
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {DB_SCHEMA} CASCADE"))

    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def db_session_factory(db_engine) -> async_sessionmaker[AsyncSession]:
    """Provide an async session factory bound to the test engine."""
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def _integration_cleanup(request):
    """Truncate all tables after each integration test for isolation.

    Uses request.getfixturevalue() for lazy resolution — non-integration
    tests never trigger the db_session_factory → db_engine → _pg_container
    fixture chain.

    Sync integration tests (e.g. WebSocket tests using TestClient) manage
    their own cleanup via app lifespan, so we skip them entirely to avoid
    Runner.run() conflicts and unawaited-coroutine warnings.
    """
    yield

    if not any(m.name == "integration" for m in request.node.iter_markers()):
        return

    # Sync tests (WebSocket/TestClient) handle their own cleanup in lifespan.
    if not asyncio.iscoroutinefunction(request.node.obj):
        return

    try:
        factory = request.getfixturevalue("db_session_factory")
    except Exception:
        return  # This test doesn't use the shared db fixture

    async with factory() as db_session:
        # Dynamically truncate all tables that exist in the test schema.
        # Some integration tests only create a subset of tables, so we
        # query information_schema to avoid "relation does not exist" errors.
        result = await db_session.execute(text(
            "SELECT table_name FROM information_schema.tables"
            f" WHERE table_schema = '{DB_SCHEMA}'"
        ))
        existing = {row[0] for row in result.fetchall()}

        truncatable = [
            t for t in [
                "messages", "sessions", "memory_entries", "soul_versions",
                "budget_reservations",
            ]
            if t in existing
        ]
        if truncatable:
            qualified = ", ".join(f"{DB_SCHEMA}.{t}" for t in truncatable)
            await db_session.execute(text(f"TRUNCATE {qualified} CASCADE"))

        if "budget_state" in existing:
            await db_session.execute(text(
                f"UPDATE {DB_SCHEMA}.budget_state"
                " SET cumulative_eur = 0, updated_at = NOW()"
                " WHERE id = 'global'"
            ))

        await db_session.commit()


@pytest_asyncio.fixture
async def session_manager(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator:
    """Provide a fresh SessionManager per test."""
    from src.session.manager import SessionManager

    manager = SessionManager(db_session_factory=db_session_factory)
    yield manager
