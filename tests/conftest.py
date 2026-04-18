"""Shared pytest fixtures for NeoMAGI tests.

Provides containerized PostgreSQL for integration tests via two modes:
1. TEST_DATABASE_* env vars present → connect to external PG (CI scenario)
2. Otherwise → testcontainers auto-starts a temporary PG container (local dev)

Safety: refuses to run against any database whose name doesn't contain '_test'.
"""

from __future__ import annotations

import asyncio
import inspect
import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.constants import DB_SCHEMA
from src.gateway.budget_gate import Reservation
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

    container = PostgresContainer("postgres:17", dbname="neomagi_test")
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
    """Create async engine, set up schema + tables + triggers. Tear down after session."""
    from src.session.database import _create_search_trigger

    engine = create_async_engine(pg_url, echo=False)

    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}"))
        await conn.run_sync(Base.metadata.create_all)
        await _create_search_trigger(conn, DB_SCHEMA)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {DB_SCHEMA} CASCADE"))

    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def db_session_factory(db_engine) -> async_sessionmaker[AsyncSession]:
    """Provide an async session factory bound to the test engine."""
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True, loop_scope="session")
async def _integration_cleanup(request):
    """Truncate all tables after each async integration test for isolation.

    Resolves db_session_factory lazily during *setup* (before yield) so the
    fixture dependency chain is established while the event loop is accepting
    new coroutines.  The actual TRUNCATE runs in teardown (after yield).

    Non-integration tests skip both setup resolution and teardown truncation.
    Sync integration tests (WebSocket/TestClient) manage their own cleanup.
    """
    is_integration = any(m.name == "integration" for m in request.node.iter_markers())

    # pytest-asyncio auto mode wraps async defs → iscoroutinefunction returns
    # False on the wrapper.  Use inspect.unwrap to check the original function.
    is_async = asyncio.iscoroutinefunction(inspect.unwrap(request.node.obj))

    factory = None
    if is_integration and is_async:
        try:
            factory = request.getfixturevalue("db_session_factory")
        except Exception:
            pass  # Test doesn't use the shared db fixture

    yield

    if factory is not None:
        await _truncate_integration_tables(factory)


async def _truncate_integration_tables(factory) -> None:
    """Truncate test schema tables for integration test isolation."""
    async with factory() as db_session:
        result = await db_session.execute(
            text(
                "SELECT table_name FROM information_schema.tables"
                f" WHERE table_schema = '{DB_SCHEMA}'"
            )
        )
        existing = {row[0] for row in result.fetchall()}

        truncatable = [
            t
            for t in [
                "messages",
                "sessions",
                "principal_bindings",
                "principals",
                "memory_entries",
                "memory_source_ledger",
                "soul_versions",
                "budget_reservations",
                "skill_spec_versions",
                "skill_evidence",
                "skill_specs",
                "wrapper_tool_versions",
                "wrapper_tools",
                "active_procedures",
            ]
            if t in existing
        ]
        if truncatable:
            qualified = ", ".join(f"{DB_SCHEMA}.{t}" for t in truncatable)
            await db_session.execute(text(f"TRUNCATE {qualified} CASCADE"))

        if "budget_state" in existing:
            await db_session.execute(
                text(
                    f"UPDATE {DB_SCHEMA}.budget_state"
                    " SET cumulative_eur = 0, updated_at = NOW()"
                    " WHERE id = 'global'"
                )
            )

        await db_session.commit()


class StubBudgetGate:
    """Always-approve stub for tests that don't exercise budget behavior."""

    async def try_reserve(self, **kwargs: object) -> Reservation:
        return Reservation(
            denied=False,
            reservation_id="stub-00000000-0000-0000-0000-000000000000",
            reserved_eur=float(kwargs.get("estimated_cost_eur", 0.05)),
        )

    async def settle(self, **kwargs: object) -> None:
        pass


@pytest_asyncio.fixture
async def session_manager(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator:
    """Provide a fresh SessionManager per test."""
    from src.session.manager import SessionManager

    manager = SessionManager(db_session_factory=db_session_factory)
    yield manager
