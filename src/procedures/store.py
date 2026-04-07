"""PostgreSQL-backed active procedure store (P2-M2a).

Uses raw ``sqlalchemy.text()`` queries (project convention).
All DB operations are async.

Enforces:
- Single active (non-completed) procedure per session via partial unique index.
- Optimistic CAS on ``revision`` for state transitions.
- ``completed_at`` set on terminal state write.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import text

from src.constants import DB_SCHEMA
from src.infra.sql import jsonb_text
from src.procedures.types import ActiveProcedure, CasConflict, ProcedureExecutionMetadata

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Column list
# ---------------------------------------------------------------------------

_COLS = (
    "instance_id, session_id, spec_id, spec_version, state, "
    "context, execution_metadata, revision, created_at, updated_at, completed_at"
)


# ---------------------------------------------------------------------------
# Row mapper
# ---------------------------------------------------------------------------


def _row_to_active(row: Any) -> ActiveProcedure:
    """Convert a DB row to an ActiveProcedure domain object."""
    meta_raw = row.execution_metadata or {}
    return ActiveProcedure(
        instance_id=row.instance_id,
        session_id=row.session_id,
        spec_id=row.spec_id,
        spec_version=row.spec_version,
        state=row.state,
        context=row.context or {},
        execution_metadata=ProcedureExecutionMetadata.model_validate(meta_raw),
        revision=row.revision,
    )


# ---------------------------------------------------------------------------
# ProcedureStore
# ---------------------------------------------------------------------------


class ProcedureStore:
    """PostgreSQL-backed store for active procedure instances."""

    def __init__(self, db_session_factory: async_sessionmaker) -> None:  # type: ignore[type-arg]
        self._db_factory = db_session_factory

    async def create(self, active: ActiveProcedure) -> ActiveProcedure:
        """Insert a new active procedure. Raises on duplicate session conflict."""
        sql = jsonb_text(
            f"""
            INSERT INTO {DB_SCHEMA}.active_procedures
                (instance_id, session_id, spec_id, spec_version, state,
                 context, execution_metadata, revision)
            VALUES
                (:instance_id, :session_id, :spec_id, :spec_version, :state,
                 :context, :execution_metadata, :revision)
            RETURNING {_COLS}
            """,
            "context",
            "execution_metadata",
        )
        params = _active_to_params(active)
        async with self._db_factory() as db:
            try:
                result = await db.execute(sql, params)
                row = result.first()
                assert row is not None
                await db.commit()
                logger.info(
                    "procedure_created",
                    instance_id=active.instance_id,
                    session_id=active.session_id,
                    spec_id=active.spec_id,
                )
                return _row_to_active(row)
            except Exception:
                await db.rollback()
                raise

    async def get_active(self, session_id: str) -> ActiveProcedure | None:
        """Return the active (non-completed) procedure for a session, or None."""
        sql = text(
            f"SELECT {_COLS} FROM {DB_SCHEMA}.active_procedures "
            "WHERE session_id = :session_id AND completed_at IS NULL"
        )
        async with self._db_factory() as db:
            result = await db.execute(sql, {"session_id": session_id})
            row = result.first()
            if row is None:
                return None
            return _row_to_active(row)

    async def get(self, instance_id: str) -> ActiveProcedure | None:
        """Return a procedure instance by id (regardless of completion status)."""
        sql = text(
            f"SELECT {_COLS} FROM {DB_SCHEMA}.active_procedures "
            "WHERE instance_id = :instance_id"
        )
        async with self._db_factory() as db:
            result = await db.execute(sql, {"instance_id": instance_id})
            row = result.first()
            if row is None:
                return None
            return _row_to_active(row)

    async def cas_update(
        self,
        instance_id: str,
        expected_revision: int,
        *,
        state: str,
        context: dict[str, Any],
        completed_at: bool = False,
    ) -> ActiveProcedure | CasConflict:
        """CAS update: only succeeds if current revision matches expected.

        Sets ``completed_at = now()`` when *completed_at* is True.
        Returns the updated ``ActiveProcedure`` on success, or a
        ``CasConflict`` if the revision has changed.
        """
        completed_expr = "now()" if completed_at else "NULL"
        sql = jsonb_text(
            f"""
            UPDATE {DB_SCHEMA}.active_procedures SET
                state = :state,
                context = :context,
                revision = :expected_revision + 1,
                updated_at = now(),
                completed_at = {completed_expr}
            WHERE instance_id = :instance_id
              AND revision = :expected_revision
              AND completed_at IS NULL
            RETURNING {_COLS}
            """,
            "context",
        )
        params = {
            "instance_id": instance_id,
            "expected_revision": expected_revision,
            "state": state,
            "context": context,
        }
        async with self._db_factory() as db:
            result = await db.execute(sql, params)
            row = result.first()
            if row is not None:
                await db.commit()
                updated = _row_to_active(row)
                logger.info(
                    "procedure_cas_updated",
                    instance_id=instance_id,
                    new_state=state,
                    new_revision=updated.revision,
                    completed=completed_at,
                )
                return updated
            # CAS failed — read current revision for diagnostics
            await db.rollback()
            actual = await self._read_current_revision(db, instance_id)
            logger.warning(
                "procedure_cas_conflict",
                instance_id=instance_id,
                expected_revision=expected_revision,
                actual_revision=actual,
            )
            return CasConflict(
                instance_id=instance_id,
                expected_revision=expected_revision,
                actual_revision=actual,
            )

    async def _read_current_revision(
        self, db: AsyncSession, instance_id: str,
    ) -> int | None:
        """Read the current revision for diagnostics after CAS failure."""
        sql = text(
            f"SELECT revision FROM {DB_SCHEMA}.active_procedures "
            "WHERE instance_id = :instance_id AND completed_at IS NULL"
        )
        result = await db.execute(sql, {"instance_id": instance_id})
        row = result.first()
        return row.revision if row is not None else None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _active_to_params(active: ActiveProcedure) -> dict[str, Any]:
    """Convert an ActiveProcedure to SQL params."""
    return {
        "instance_id": active.instance_id,
        "session_id": active.session_id,
        "spec_id": active.spec_id,
        "spec_version": active.spec_version,
        "state": active.state,
        "context": active.context,
        "execution_metadata": active.execution_metadata.model_dump(),
        "revision": active.revision,
    }
