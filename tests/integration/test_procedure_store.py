"""PostgreSQL integration tests for ProcedureStore.

Covers:
- Partial unique index (single-active per session)
- CAS conflict detection
- Terminal state releases single-active constraint
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import DB_SCHEMA
from src.procedures.store import ProcedureStore
from src.procedures.types import ActiveProcedure, CasConflict, ProcedureExecutionMetadata
from src.session.database import ensure_schema


@pytest.mark.integration
@pytest.mark.asyncio
async def test_create_and_get_active(
    db_engine,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await ensure_schema(db_engine, DB_SCHEMA)
    store = ProcedureStore(db_session_factory)

    active = ActiveProcedure(
        instance_id="proc_test_create_1",
        session_id="sess_create_1",
        spec_id="test.spec",
        spec_version=1,
        state="draft",
        context={"key": "value"},
        execution_metadata=ProcedureExecutionMetadata(actor="user-1"),
    )
    created = await store.create(active)
    assert created.instance_id == "proc_test_create_1"
    assert created.state == "draft"
    assert created.revision == 0

    loaded = await store.get_active("sess_create_1")
    assert loaded is not None
    assert loaded.instance_id == "proc_test_create_1"
    assert loaded.context == {"key": "value"}
    assert loaded.execution_metadata.actor == "user-1"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_single_active_per_session(
    db_engine,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Partial unique index enforces at most one active procedure per session."""
    await ensure_schema(db_engine, DB_SCHEMA)
    store = ProcedureStore(db_session_factory)

    first = ActiveProcedure(
        instance_id="proc_single_1",
        session_id="sess_single",
        spec_id="test.spec",
        spec_version=1,
        state="draft",
    )
    await store.create(first)

    second = ActiveProcedure(
        instance_id="proc_single_2",
        session_id="sess_single",
        spec_id="test.spec",
        spec_version=1,
        state="draft",
    )
    with pytest.raises(Exception):
        await store.create(second)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cas_update_success(
    db_engine,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await ensure_schema(db_engine, DB_SCHEMA)
    store = ProcedureStore(db_session_factory)

    active = ActiveProcedure(
        instance_id="proc_cas_ok",
        session_id="sess_cas_ok",
        spec_id="test.spec",
        spec_version=1,
        state="draft",
        context={"step": 0},
    )
    await store.create(active)

    result = await store.cas_update(
        "proc_cas_ok", 0,
        state="review",
        context={"step": 1},
    )
    assert isinstance(result, ActiveProcedure)
    assert result.state == "review"
    assert result.revision == 1
    assert result.context == {"step": 1}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_cas_conflict(
    db_engine,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """CAS fails when expected_revision doesn't match actual."""
    await ensure_schema(db_engine, DB_SCHEMA)
    store = ProcedureStore(db_session_factory)

    active = ActiveProcedure(
        instance_id="proc_cas_conflict",
        session_id="sess_cas_conflict",
        spec_id="test.spec",
        spec_version=1,
        state="draft",
    )
    await store.create(active)

    result = await store.cas_update(
        "proc_cas_conflict", 99,
        state="review",
        context={},
    )
    assert isinstance(result, CasConflict)
    assert result.expected_revision == 99
    assert result.actual_revision == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_terminal_releases_single_active(
    db_engine,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """After terminal completion, same session can start a new procedure."""
    await ensure_schema(db_engine, DB_SCHEMA)
    store = ProcedureStore(db_session_factory)

    first = ActiveProcedure(
        instance_id="proc_terminal_1",
        session_id="sess_terminal",
        spec_id="test.spec",
        spec_version=1,
        state="draft",
    )
    await store.create(first)

    # Complete it (terminal)
    result = await store.cas_update(
        "proc_terminal_1", 0,
        state="done",
        context={},
        completed_at=True,
    )
    assert isinstance(result, ActiveProcedure)
    assert result.state == "done"

    # get_active should return None (completed)
    active = await store.get_active("sess_terminal")
    assert active is None

    # Now a new procedure can be created for the same session
    second = ActiveProcedure(
        instance_id="proc_terminal_2",
        session_id="sess_terminal",
        spec_id="test.spec",
        spec_version=1,
        state="draft",
    )
    created = await store.create(second)
    assert created.instance_id == "proc_terminal_2"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_get_by_instance_id(
    db_engine,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """get() returns procedure regardless of completion status."""
    await ensure_schema(db_engine, DB_SCHEMA)
    store = ProcedureStore(db_session_factory)

    active = ActiveProcedure(
        instance_id="proc_get_by_id",
        session_id="sess_get_by_id",
        spec_id="test.spec",
        spec_version=1,
        state="draft",
    )
    await store.create(active)

    # Complete it
    await store.cas_update(
        "proc_get_by_id", 0,
        state="done",
        context={},
        completed_at=True,
    )

    # get() should still return it
    loaded = await store.get("proc_get_by_id")
    assert loaded is not None
    assert loaded.state == "done"

    # get_active should not
    active_result = await store.get_active("sess_get_by_id")
    assert active_result is None
