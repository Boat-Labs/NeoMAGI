"""Tests for P2-M3a Slice A: principals + principal_bindings schema.

Covers: idempotent ensure_schema, ORM model CRUD, partial unique constraint,
binding unique constraint, FK ON DELETE RESTRICT, sessions.principal_id nullable.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from src.constants import DB_SCHEMA
from src.memory.writer import _uuid7
from src.session.database import ensure_schema
from src.session.models import PrincipalBindingRecord, PrincipalRecord, SessionRecord


@pytest.mark.integration
async def test_ensure_schema_creates_principal_tables(db_engine: AsyncEngine) -> None:
    """ensure_schema creates principals + principal_bindings; runs twice (idempotent)."""
    await ensure_schema(db_engine, DB_SCHEMA)
    await ensure_schema(db_engine, DB_SCHEMA)

    async with db_engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables"
                f" WHERE table_schema = '{DB_SCHEMA}'"
                "   AND table_name IN ('principals', 'principal_bindings')"
                " ORDER BY table_name"
            )
        )
        tables = [row[0] for row in result.fetchall()]
    assert tables == ["principal_bindings", "principals"]


@pytest.mark.integration
async def test_ensure_schema_adds_principal_id_to_sessions(db_engine: AsyncEngine) -> None:
    """ensure_schema adds principal_id column to sessions table."""
    await ensure_schema(db_engine, DB_SCHEMA)

    async with db_engine.begin() as conn:
        result = await conn.execute(
            text(
                "SELECT column_name FROM information_schema.columns"
                f" WHERE table_schema = '{DB_SCHEMA}'"
                "   AND table_name = 'sessions'"
                "   AND column_name = 'principal_id'"
            )
        )
        assert result.scalar_one_or_none() == "principal_id"


@pytest.mark.integration
async def test_principal_record_crud(db_session_factory) -> None:
    """Insert and read back a PrincipalRecord."""
    pid = str(_uuid7())
    async with db_session_factory() as session:
        record = PrincipalRecord(id=pid, name="Test Owner", role="owner")
        session.add(record)
        await session.commit()

    async with db_session_factory() as session:
        result = await session.execute(select(PrincipalRecord).where(PrincipalRecord.id == pid))
        loaded = result.scalar_one()
        assert loaded.name == "Test Owner"
        assert loaded.role == "owner"
        assert loaded.password_hash is None
        assert loaded.created_at is not None


@pytest.mark.integration
async def test_single_owner_partial_unique_index(db_session_factory) -> None:
    """Only one row with role='owner' allowed."""
    async with db_session_factory() as session:
        session.add(PrincipalRecord(id=str(_uuid7()), name="Owner 1", role="owner"))
        await session.commit()

    with pytest.raises(IntegrityError, match="uq_principals_single_owner"):
        async with db_session_factory() as session:
            session.add(PrincipalRecord(id=str(_uuid7()), name="Owner 2", role="owner"))
            await session.commit()


@pytest.mark.integration
async def test_binding_unique_constraint(db_session_factory) -> None:
    """Duplicate (channel_type, channel_identity) not allowed."""
    pid = str(_uuid7())
    async with db_session_factory() as session:
        session.add(PrincipalRecord(id=pid, name="Owner", role="owner"))
        await session.commit()

    async with db_session_factory() as session:
        session.add(
            PrincipalBindingRecord(
                id=str(_uuid7()),
                principal_id=pid,
                channel_type="telegram",
                channel_identity="123",
                verified=True,
            )
        )
        await session.commit()

    with pytest.raises(IntegrityError, match="uq_principal_bindings_channel"):
        async with db_session_factory() as session:
            session.add(
                PrincipalBindingRecord(
                    id=str(_uuid7()),
                    principal_id=pid,
                    channel_type="telegram",
                    channel_identity="123",
                    verified=False,
                )
            )
            await session.commit()


@pytest.mark.integration
async def test_principal_fk_on_delete_restrict_binding(db_session_factory) -> None:
    """Cannot delete principal that has bindings (ON DELETE RESTRICT)."""
    pid = str(_uuid7())
    async with db_session_factory() as session:
        principal = PrincipalRecord(id=pid, name="Owner", role="owner")
        session.add(principal)
        await session.commit()

    async with db_session_factory() as session:
        session.add(
            PrincipalBindingRecord(
                id=str(_uuid7()),
                principal_id=pid,
                channel_type="webchat",
                channel_identity="owner",
                verified=True,
            )
        )
        await session.commit()

    # Raw SQL bypasses ORM cascade="all, delete-orphan" on
    # PrincipalRecord.bindings, so the DB-level RESTRICT fires.
    with pytest.raises(IntegrityError):
        async with db_session_factory() as session:
            await session.execute(
                text(f"DELETE FROM {DB_SCHEMA}.principals WHERE id = :pid"),
                {"pid": pid},
            )
            await session.commit()


@pytest.mark.integration
async def test_session_principal_id_fk_on_delete_restrict(db_session_factory) -> None:
    """Cannot delete principal that is referenced by a session."""
    pid = str(_uuid7())
    async with db_session_factory() as session:
        session.add(PrincipalRecord(id=pid, name="Owner", role="owner"))
        await session.commit()

    async with db_session_factory() as session:
        session.add(SessionRecord(id="owned-session", principal_id=pid))
        await session.commit()

    with pytest.raises(IntegrityError):
        async with db_session_factory() as session:
            result = await session.execute(
                select(PrincipalRecord).where(PrincipalRecord.id == pid)
            )
            principal = result.scalar_one()
            await session.delete(principal)
            await session.commit()


@pytest.mark.integration
async def test_session_principal_id_nullable(db_session_factory) -> None:
    """Session with principal_id=NULL is valid (anonymous session)."""
    async with db_session_factory() as session:
        session.add(SessionRecord(id="anon-session", principal_id=None))
        await session.commit()

    async with db_session_factory() as session:
        result = await session.execute(
            select(SessionRecord).where(SessionRecord.id == "anon-session")
        )
        loaded = result.scalar_one()
        assert loaded.principal_id is None
