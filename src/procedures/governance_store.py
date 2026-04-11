"""PostgreSQL-backed procedure spec governance store (P2-M2c).

Provides current-state CRUD and governance ledger helpers for
procedure_spec_definitions and procedure_spec_governance tables.

Uses raw ``sqlalchemy.text()`` queries (project convention).
All DB operations are async.  Frozen domain fields are stored as
JSONB via ``model_dump(mode="json")`` and converted back via
``ProcedureSpec.model_validate()`` on read.
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

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Store-internal record (not public domain surface)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcedureSpecProposalRecord:
    """Store-internal record for a governance ledger entry.

    NOT part of the public domain surface — only used inside
    ProcedureSpecGovernanceStore and the ProcedureSpecGovernedObjectAdapter.
    """

    governance_version: int
    procedure_spec_id: str
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

_DEF_COLS = "id, version, payload, disabled, created_at, updated_at"

_GOV_COLS = (
    "governance_version, procedure_spec_id, status, proposal, eval_result, "
    "created_by, created_at, applied_at, rolled_back_from"
)

# ---------------------------------------------------------------------------
# Row mapper
# ---------------------------------------------------------------------------


def _row_to_proposal_record(row: object) -> ProcedureSpecProposalRecord:
    """Convert a DB row to a ProcedureSpecProposalRecord."""
    return ProcedureSpecProposalRecord(
        governance_version=row.governance_version,  # type: ignore[union-attr]
        procedure_spec_id=row.procedure_spec_id,  # type: ignore[union-attr]
        status=row.status,  # type: ignore[union-attr]
        proposal=row.proposal,  # type: ignore[union-attr]
        eval_result=row.eval_result,  # type: ignore[union-attr]
        created_by=row.created_by,  # type: ignore[union-attr]
        created_at=row.created_at,  # type: ignore[union-attr]
        applied_at=row.applied_at,  # type: ignore[union-attr]
        rolled_back_from=row.rolled_back_from,  # type: ignore[union-attr]
    )


# ---------------------------------------------------------------------------
# ProcedureSpecGovernanceStore
# ---------------------------------------------------------------------------


class ProcedureSpecGovernanceStore:
    """PostgreSQL-backed store for procedure spec governance.

    Provides current-state CRUD (procedure_spec_definitions) and
    governance ledger helpers (procedure_spec_governance).
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
        spec_payload: dict,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        """Insert or update a materialized procedure spec definition.

        *spec_payload* must be the result of ``ProcedureSpec.model_dump(mode="json")``.

        When *session* is provided the caller owns the transaction; otherwise
        a standalone session + commit is used.
        """
        sql = jsonb_text(
            f"""
            INSERT INTO {DB_SCHEMA}.procedure_spec_definitions
                (id, version, payload, disabled, updated_at)
            VALUES
                (:id, :version, :payload, false, now())
            ON CONFLICT (id) DO UPDATE SET
                version = EXCLUDED.version,
                payload = EXCLUDED.payload,
                disabled = EXCLUDED.disabled,
                updated_at = now()
        """,
            "payload",
        )
        params = {
            "id": spec_payload["id"],
            "version": spec_payload["version"],
            "payload": spec_payload,
        }
        if session is not None:
            await session.execute(sql, params)
        else:
            async with self._db_factory() as db:
                await db.execute(sql, params)
                await db.commit()

    async def disable(
        self,
        spec_id: str,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        """Mark a procedure spec definition as disabled (soft-delete)."""
        sql = text(
            f"UPDATE {DB_SCHEMA}.procedure_spec_definitions "
            "SET disabled = true, updated_at = now() WHERE id = :id"
        )
        if session is not None:
            await session.execute(sql, {"id": spec_id})
        else:
            async with self._db_factory() as db:
                await db.execute(sql, {"id": spec_id})
                await db.commit()

    async def list_active(self) -> list[dict]:
        """Return all active (non-disabled) procedure spec payloads."""
        sql = text(
            f"SELECT {_DEF_COLS} FROM {DB_SCHEMA}.procedure_spec_definitions "
            "WHERE disabled = false ORDER BY id"
        )
        async with self._db_factory() as db:
            result = await db.execute(sql)
            return [row.payload for row in result]  # type: ignore[union-attr]

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
            INSERT INTO {DB_SCHEMA}.procedure_spec_governance
                (procedure_spec_id, status, proposal, created_by)
            VALUES
                (:procedure_spec_id, 'proposed', :proposal, :created_by)
            RETURNING governance_version
        """,
            "proposal",
        )
        params = {
            "procedure_spec_id": proposal.object_id,
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
            assert row is not None
            return row.governance_version  # type: ignore[union-attr]
        async with self._db_factory() as db:
            result = await db.execute(sql, params)
            row = result.first()
            assert row is not None
            gv = row.governance_version  # type: ignore[union-attr]
            await db.commit()
        return gv

    async def get_proposal(
        self, governance_version: int
    ) -> ProcedureSpecProposalRecord | None:
        """Return a single governance ledger entry by governance_version."""
        sql = text(
            f"SELECT {_GOV_COLS} FROM {DB_SCHEMA}.procedure_spec_governance "
            "WHERE governance_version = :gv"
        )
        async with self._db_factory() as db:
            result = await db.execute(sql, {"gv": governance_version})
            row = result.first()
            if row is None:
                return None
            return _row_to_proposal_record(row)

    async def store_eval_result(
        self, governance_version: int, result: GrowthEvalResult
    ) -> None:
        """Persist eval result to the governance ledger entry."""
        sql = jsonb_text(
            f"""
            UPDATE {DB_SCHEMA}.procedure_spec_governance SET
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
        """Update the status of a governance ledger entry.

        ``applied_at`` semantics are status-aware:
        - ``active``: writes the provided timestamp (caller must supply).
        - ``proposed`` / ``vetoed``: clears to NULL (no valid apply).
        - ``rolled_back``: preserves existing value for audit trail.

        ``rolled_back_from`` uses COALESCE to preserve existing value.
        """
        # Status-aware applied_at: preserve on rollback, clear on proposed/vetoed
        if status in (
            GrowthLifecycleStatus.proposed,
            GrowthLifecycleStatus.vetoed,
        ):
            applied_at_expr = "NULL"
        elif status == GrowthLifecycleStatus.rolled_back:
            applied_at_expr = "COALESCE(:applied_at, applied_at)"
        else:
            # active (or any future status): write the provided value
            applied_at_expr = ":applied_at"

        sql = text(f"""
            UPDATE {DB_SCHEMA}.procedure_spec_governance SET
                status = :status,
                applied_at = {applied_at_expr},
                rolled_back_from = COALESCE(:rolled_back_from, rolled_back_from)
            WHERE governance_version = :gv
        """)
        params: dict[str, object] = {
            "gv": governance_version,
            "status": status.value,
            "rolled_back_from": rolled_back_from,
        }
        # Only bind :applied_at when the expression references it
        if ":applied_at" in applied_at_expr:
            params["applied_at"] = applied_at
        if session is not None:
            await session.execute(sql, params)
        else:
            async with self._db_factory() as db:
                await db.execute(sql, params)
                await db.commit()

    async def find_last_applied(
        self, procedure_spec_id: str
    ) -> ProcedureSpecProposalRecord | None:
        """Find the most recent applied governance entry for a procedure spec."""
        sql = text(
            f"SELECT {_GOV_COLS} FROM {DB_SCHEMA}.procedure_spec_governance "
            "WHERE procedure_spec_id = :pid AND status = 'active' "
            "ORDER BY governance_version DESC LIMIT 1"
        )
        async with self._db_factory() as db:
            result = await db.execute(sql, {"pid": procedure_spec_id})
            row = result.first()
            if row is None:
                return None
            return _row_to_proposal_record(row)
