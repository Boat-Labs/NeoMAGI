"""PostgreSQL-backed wrapper tool store (P2-M1c).

Provides CRUD + governance ledger helpers for wrapper_tools and
wrapper_tool_versions tables.

Uses raw ``sqlalchemy.text()`` queries (project convention).
All DB operations are async.  Tuple-typed domain fields are stored as
JSONB arrays and converted back to tuples on read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text

from src.constants import DB_SCHEMA
from src.growth.types import GrowthEvalResult, GrowthLifecycleStatus, GrowthProposal
from src.infra.sql import jsonb_text
from src.wrappers.types import WrapperToolSpec

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Store-internal record (not public domain surface)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WrapperToolProposalRecord:
    """Store-internal record for a governance ledger entry.

    NOT part of the public domain surface — only used inside
    WrapperToolStore and the WrapperToolGovernedObjectAdapter.
    """

    governance_version: int
    wrapper_tool_id: str
    status: str
    proposal: dict
    eval_result: dict | None
    created_by: str
    created_at: datetime | None
    applied_at: datetime | None
    rolled_back_from: int | None


# ---------------------------------------------------------------------------
# Column lists (DRY helpers)
# ---------------------------------------------------------------------------

_SPEC_COLS = (
    "id, capability, version, summary, input_schema, output_schema, "
    "bound_atomic_tools, implementation_ref, deny_semantics, scope_claim, disabled"
)

_VERSION_COLS = (
    "governance_version, wrapper_tool_id, status, proposal, eval_result, "
    "created_by, created_at, applied_at, rolled_back_from"
)

# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------


def _row_to_spec(row: object) -> WrapperToolSpec:
    """Convert a DB row to a WrapperToolSpec, discarding DB-only columns."""
    return WrapperToolSpec(
        id=row.id,  # type: ignore[union-attr]
        capability=row.capability,  # type: ignore[union-attr]
        version=row.version,  # type: ignore[union-attr]
        summary=row.summary,  # type: ignore[union-attr]
        input_schema=row.input_schema,  # type: ignore[union-attr]
        output_schema=row.output_schema,  # type: ignore[union-attr]
        bound_atomic_tools=tuple(row.bound_atomic_tools or []),  # type: ignore[union-attr]
        implementation_ref=row.implementation_ref,  # type: ignore[union-attr]
        deny_semantics=tuple(row.deny_semantics or []),  # type: ignore[union-attr]
        scope_claim=row.scope_claim,  # type: ignore[union-attr]
        disabled=row.disabled,  # type: ignore[union-attr]
    )


def _row_to_proposal_record(row: object) -> WrapperToolProposalRecord:
    """Convert a DB row to a WrapperToolProposalRecord."""
    return WrapperToolProposalRecord(
        governance_version=row.governance_version,  # type: ignore[union-attr]
        wrapper_tool_id=row.wrapper_tool_id,  # type: ignore[union-attr]
        status=row.status,  # type: ignore[union-attr]
        proposal=row.proposal,  # type: ignore[union-attr]
        eval_result=row.eval_result,  # type: ignore[union-attr]
        created_by=row.created_by,  # type: ignore[union-attr]
        created_at=row.created_at,  # type: ignore[union-attr]
        applied_at=row.applied_at,  # type: ignore[union-attr]
        rolled_back_from=row.rolled_back_from,  # type: ignore[union-attr]
    )


# ---------------------------------------------------------------------------
# SQL builders
# ---------------------------------------------------------------------------


def _build_spec_upsert(spec: WrapperToolSpec) -> tuple:
    """Build the SQL + params for a spec upsert."""
    sql = jsonb_text(
        f"""
        INSERT INTO {DB_SCHEMA}.wrapper_tools
            (id, capability, version, summary, input_schema, output_schema,
             bound_atomic_tools, implementation_ref, deny_semantics,
             scope_claim, disabled, updated_at)
        VALUES
            (:id, :capability, :version, :summary, :input_schema, :output_schema,
             :bound_atomic_tools, :implementation_ref, :deny_semantics,
             :scope_claim, :disabled, now())
        ON CONFLICT (id) DO UPDATE SET
            capability = EXCLUDED.capability,
            version = EXCLUDED.version,
            summary = EXCLUDED.summary,
            input_schema = EXCLUDED.input_schema,
            output_schema = EXCLUDED.output_schema,
            bound_atomic_tools = EXCLUDED.bound_atomic_tools,
            implementation_ref = EXCLUDED.implementation_ref,
            deny_semantics = EXCLUDED.deny_semantics,
            scope_claim = EXCLUDED.scope_claim,
            disabled = EXCLUDED.disabled,
            updated_at = now()
    """,
        "input_schema",
        "output_schema",
        "bound_atomic_tools",
        "deny_semantics",
    )
    params = {
        "id": spec.id,
        "capability": spec.capability,
        "version": spec.version,
        "summary": spec.summary,
        "input_schema": spec.input_schema,
        "output_schema": spec.output_schema,
        "bound_atomic_tools": list(spec.bound_atomic_tools),
        "implementation_ref": spec.implementation_ref,
        "deny_semantics": list(spec.deny_semantics),
        "scope_claim": spec.scope_claim,
        "disabled": spec.disabled,
    }
    return sql, params


# ---------------------------------------------------------------------------
# WrapperToolStore
# ---------------------------------------------------------------------------


class WrapperToolStore:
    """PostgreSQL-backed wrapper tool store.

    Provides current-state CRUD and governance ledger helpers.
    """

    def __init__(self, db_session_factory: async_sessionmaker) -> None:  # type: ignore[type-arg]
        self._db_factory = db_session_factory

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """Yield a DB session for multi-step atomic operations.

        All statements executed on the yielded session share a single
        transaction.  Commit happens on successful exit; any exception
        triggers rollback.
        """
        async with self._db_factory() as session:
            async with session.begin():
                yield session

    # ── current-state CRUD ──

    async def upsert_active(
        self,
        spec: WrapperToolSpec,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        """Insert or update a materialized wrapper tool.

        When *session* is provided the caller owns the transaction; otherwise
        a standalone session + commit is used (backwards-compatible).
        """
        sql, params = _build_spec_upsert(spec)
        if session is not None:
            await session.execute(sql, params)
        else:
            async with self._db_factory() as db:
                await db.execute(sql, params)
                await db.commit()

    async def get_active(
        self,
        wrapper_tool_id: str | None = None,
    ) -> list[WrapperToolSpec] | WrapperToolSpec | None:
        """Return active (non-disabled) wrapper tools.

        If *wrapper_tool_id* is given, return a single spec or None.
        Otherwise, return all active specs as a list.
        """
        if wrapper_tool_id is not None:
            sql = text(
                f"SELECT {_SPEC_COLS} FROM {DB_SCHEMA}.wrapper_tools "
                "WHERE id = :wid AND disabled = false"
            )
            async with self._db_factory() as db:
                result = await db.execute(sql, {"wid": wrapper_tool_id})
                row = result.first()
                if row is None:
                    return None
                return _row_to_spec(row)

        sql = text(
            f"SELECT {_SPEC_COLS} FROM {DB_SCHEMA}.wrapper_tools WHERE disabled = false ORDER BY id"
        )
        async with self._db_factory() as db:
            result = await db.execute(sql)
            return [_row_to_spec(row) for row in result]

    async def remove_active(
        self,
        wrapper_tool_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        """Mark a wrapper tool as disabled (soft-delete)."""
        sql = text(
            f"UPDATE {DB_SCHEMA}.wrapper_tools SET disabled = true, updated_at = now() "
            "WHERE id = :wid"
        )
        if session is not None:
            await session.execute(sql, {"wid": wrapper_tool_id})
        else:
            async with self._db_factory() as db:
                await db.execute(sql, {"wid": wrapper_tool_id})
                await db.commit()

    # ── governance ledger helpers ──

    async def create_proposal(
        self,
        proposal: GrowthProposal,
        *,
        session: AsyncSession | None = None,
    ) -> int:
        """Insert a new governance ledger entry (status='proposed').

        Returns governance_version.
        """
        sql = jsonb_text(
            f"""
            INSERT INTO {DB_SCHEMA}.wrapper_tool_versions
                (wrapper_tool_id, status, proposal, created_by)
            VALUES
                (:wrapper_tool_id, 'proposed', :proposal, :created_by)
            RETURNING governance_version
        """,
            "proposal",
        )
        params = {
            "wrapper_tool_id": proposal.object_id,
            "proposal": {
                "intent": proposal.intent,
                "risk_notes": proposal.risk_notes,
                "diff_summary": proposal.diff_summary,
                "evidence_refs": list(proposal.evidence_refs),
                "payload": proposal.payload,
            },
            "created_by": proposal.proposed_by,
        }
        if session is not None:
            result = await session.execute(sql, params)
            row = result.first()
            assert row is not None  # RETURNING always returns a row
            return row.governance_version  # type: ignore[union-attr]
        async with self._db_factory() as db:
            result = await db.execute(sql, params)
            row = result.first()
            assert row is not None  # RETURNING always returns a row
            gv = row.governance_version  # type: ignore[union-attr]
            await db.commit()
        return gv

    async def get_proposal(self, governance_version: int) -> WrapperToolProposalRecord | None:
        """Return a single governance ledger entry by governance_version."""
        sql = text(
            f"SELECT {_VERSION_COLS} FROM {DB_SCHEMA}.wrapper_tool_versions "
            "WHERE governance_version = :gv"
        )
        async with self._db_factory() as db:
            result = await db.execute(sql, {"gv": governance_version})
            row = result.first()
            if row is None:
                return None
            return _row_to_proposal_record(row)

    async def store_eval_result(self, governance_version: int, result: GrowthEvalResult) -> None:
        """Persist eval result to the governance ledger entry."""
        sql = jsonb_text(
            f"""
            UPDATE {DB_SCHEMA}.wrapper_tool_versions SET
                eval_result = :eval_result
            WHERE governance_version = :gv
        """,
            "eval_result",
        )
        eval_dict: dict[str, object] = {
            "passed": result.passed,
            "checks": result.checks,
            "summary": result.summary,
            "contract_id": result.contract_id,
            "contract_version": result.contract_version,
        }
        async with self._db_factory() as db:
            await db.execute(sql, {"gv": governance_version, "eval_result": eval_dict})
            await db.commit()

    async def update_proposal_status(
        self,
        governance_version: int,
        status: GrowthLifecycleStatus,
        *,
        applied_at: datetime | None = None,
        rolled_back_from: int | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        """Update the status of a governance ledger entry."""
        sql = text(f"""
            UPDATE {DB_SCHEMA}.wrapper_tool_versions SET
                status = :status,
                applied_at = :applied_at,
                rolled_back_from = :rolled_back_from
            WHERE governance_version = :gv
        """)
        params = {
            "gv": governance_version,
            "status": status.value,
            "applied_at": applied_at,
            "rolled_back_from": rolled_back_from,
        }
        if session is not None:
            await session.execute(sql, params)
        else:
            async with self._db_factory() as db:
                await db.execute(sql, params)
                await db.commit()

    async def find_last_applied(self, wrapper_tool_id: str) -> WrapperToolProposalRecord | None:
        """Find the most recent applied governance entry for a wrapper tool."""
        sql = text(
            f"SELECT {_VERSION_COLS} FROM {DB_SCHEMA}.wrapper_tool_versions "
            "WHERE wrapper_tool_id = :wid AND status = 'active' "
            "ORDER BY governance_version DESC LIMIT 1"
        )
        async with self._db_factory() as db:
            result = await db.execute(sql, {"wid": wrapper_tool_id})
            row = result.first()
            if row is None:
                return None
            return _row_to_proposal_record(row)
