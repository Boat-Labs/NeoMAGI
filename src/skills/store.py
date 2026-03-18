"""PostgreSQL-backed skill registry and store (P2-M1b).

Implements :class:`SkillRegistry` protocol and provides CRUD + governance
ledger helpers for skill_specs, skill_evidence, and skill_spec_versions.

Uses raw ``sqlalchemy.text()`` queries (project convention, see memory/searcher.py).
All DB operations are async.  Tuple-typed domain fields are stored as JSONB arrays
and converted back to tuples on read.
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
from src.skills.types import SkillEvidence, SkillSpec

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# Store-internal record (not public domain surface)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SkillProposalRecord:
    """Store-internal record for a governance ledger entry.

    NOT part of the public domain surface -- only used inside SkillStore
    and the SkillGovernedObjectAdapter.
    """

    governance_version: int
    skill_id: str
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
    "id, capability, version, summary, activation, "
    "activation_tags, preconditions, delta, tool_preferences, "
    "escalation_rules, exchange_policy, disabled"
)

_EVIDENCE_COLS = (
    "skill_id, source, success_count, failure_count, last_validated_at, "
    "positive_patterns, negative_patterns, known_breakages"
)

_VERSION_COLS = (
    "governance_version, skill_id, status, proposal, eval_result, "
    "created_by, created_at, applied_at, rolled_back_from"
)

# ---------------------------------------------------------------------------
# Row mappers
# ---------------------------------------------------------------------------


def _row_to_spec(row: object) -> SkillSpec:
    """Convert a DB row to a SkillSpec, discarding DB-only columns."""
    return SkillSpec(
        id=row.id,  # type: ignore[union-attr]
        capability=row.capability,  # type: ignore[union-attr]
        version=row.version,  # type: ignore[union-attr]
        summary=row.summary,  # type: ignore[union-attr]
        activation=row.activation,  # type: ignore[union-attr]
        activation_tags=tuple(row.activation_tags or []),  # type: ignore[union-attr]
        preconditions=tuple(row.preconditions or []),  # type: ignore[union-attr]
        delta=tuple(row.delta or []),  # type: ignore[union-attr]
        tool_preferences=tuple(row.tool_preferences or []),  # type: ignore[union-attr]
        escalation_rules=tuple(row.escalation_rules or []),  # type: ignore[union-attr]
        exchange_policy=row.exchange_policy,  # type: ignore[union-attr]
        disabled=row.disabled,  # type: ignore[union-attr]
    )


def _row_to_evidence(row: object) -> SkillEvidence:
    """Convert a DB row to a SkillEvidence, discarding DB-only columns."""
    return SkillEvidence(
        source=row.source,  # type: ignore[union-attr]
        success_count=row.success_count,  # type: ignore[union-attr]
        failure_count=row.failure_count,  # type: ignore[union-attr]
        last_validated_at=row.last_validated_at,  # type: ignore[union-attr]
        positive_patterns=tuple(row.positive_patterns or []),  # type: ignore[union-attr]
        negative_patterns=tuple(row.negative_patterns or []),  # type: ignore[union-attr]
        known_breakages=tuple(row.known_breakages or []),  # type: ignore[union-attr]
    )


def _row_to_proposal_record(row: object) -> SkillProposalRecord:
    """Convert a DB row to a SkillProposalRecord."""
    return SkillProposalRecord(
        governance_version=row.governance_version,  # type: ignore[union-attr]
        skill_id=row.skill_id,  # type: ignore[union-attr]
        status=row.status,  # type: ignore[union-attr]
        proposal=row.proposal,  # type: ignore[union-attr]
        eval_result=row.eval_result,  # type: ignore[union-attr]
        created_by=row.created_by,  # type: ignore[union-attr]
        created_at=row.created_at,  # type: ignore[union-attr]
        applied_at=row.applied_at,  # type: ignore[union-attr]
        rolled_back_from=row.rolled_back_from,  # type: ignore[union-attr]
    )


# ---------------------------------------------------------------------------
# SkillStore
# ---------------------------------------------------------------------------


class SkillStore:
    """PostgreSQL-backed skill registry and store.

    Implements the :class:`SkillRegistry` protocol and provides additional
    CRUD + governance ledger helpers.
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

    # ── SkillRegistry protocol ──

    async def list_active(self) -> list[SkillSpec]:
        """Return all non-disabled, materialized skills."""
        sql = text(
            f"SELECT {_SPEC_COLS} FROM {DB_SCHEMA}.skill_specs "
            "WHERE disabled = false ORDER BY id"
        )
        async with self._db_factory() as db:
            result = await db.execute(sql)
            return [_row_to_spec(row) for row in result]

    async def get_evidence(self, skill_ids: tuple[str, ...]) -> dict[str, SkillEvidence]:
        """Return evidence for the given skill IDs."""
        if not skill_ids:
            return {}
        sql = text(
            f"SELECT {_EVIDENCE_COLS} FROM {DB_SCHEMA}.skill_evidence "
            "WHERE skill_id = ANY(:skill_ids)"
        )
        async with self._db_factory() as db:
            result = await db.execute(sql, {"skill_ids": list(skill_ids)})
            return {
                row.skill_id: _row_to_evidence(row)  # type: ignore[union-attr]
                for row in result
            }

    # ── current-state CRUD ──

    async def upsert_active(
        self,
        spec: SkillSpec,
        evidence: SkillEvidence,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        """Insert or update a materialized skill + its evidence.

        When *session* is provided the caller owns the transaction; otherwise
        a standalone session + commit is used (backwards-compatible).
        """
        spec_sql = text(f"""
            INSERT INTO {DB_SCHEMA}.skill_specs
                (id, capability, version, summary, activation,
                 activation_tags, preconditions, delta, tool_preferences,
                 escalation_rules, exchange_policy, disabled, updated_at)
            VALUES
                (:id, :capability, :version, :summary, :activation,
                 :activation_tags, :preconditions, :delta, :tool_preferences,
                 :escalation_rules, :exchange_policy, :disabled, now())
            ON CONFLICT (id) DO UPDATE SET
                capability = EXCLUDED.capability,
                version = EXCLUDED.version,
                summary = EXCLUDED.summary,
                activation = EXCLUDED.activation,
                activation_tags = EXCLUDED.activation_tags,
                preconditions = EXCLUDED.preconditions,
                delta = EXCLUDED.delta,
                tool_preferences = EXCLUDED.tool_preferences,
                escalation_rules = EXCLUDED.escalation_rules,
                exchange_policy = EXCLUDED.exchange_policy,
                disabled = EXCLUDED.disabled,
                updated_at = now()
        """)
        ev_sql = text(f"""
            INSERT INTO {DB_SCHEMA}.skill_evidence
                (skill_id, source, success_count, failure_count, last_validated_at,
                 positive_patterns, negative_patterns, known_breakages, updated_at)
            VALUES
                (:skill_id, :source, :success_count, :failure_count, :last_validated_at,
                 :positive_patterns, :negative_patterns, :known_breakages, now())
            ON CONFLICT (skill_id) DO UPDATE SET
                source = EXCLUDED.source,
                success_count = EXCLUDED.success_count,
                failure_count = EXCLUDED.failure_count,
                last_validated_at = EXCLUDED.last_validated_at,
                positive_patterns = EXCLUDED.positive_patterns,
                negative_patterns = EXCLUDED.negative_patterns,
                known_breakages = EXCLUDED.known_breakages,
                updated_at = now()
        """)
        spec_params = {
            "id": spec.id,
            "capability": spec.capability,
            "version": spec.version,
            "summary": spec.summary,
            "activation": spec.activation,
            "activation_tags": list(spec.activation_tags),
            "preconditions": list(spec.preconditions),
            "delta": list(spec.delta),
            "tool_preferences": list(spec.tool_preferences),
            "escalation_rules": list(spec.escalation_rules),
            "exchange_policy": spec.exchange_policy,
            "disabled": spec.disabled,
        }
        ev_params = {
            "skill_id": spec.id,
            "source": evidence.source,
            "success_count": evidence.success_count,
            "failure_count": evidence.failure_count,
            "last_validated_at": evidence.last_validated_at,
            "positive_patterns": list(evidence.positive_patterns),
            "negative_patterns": list(evidence.negative_patterns),
            "known_breakages": list(evidence.known_breakages),
        }
        if session is not None:
            await session.execute(spec_sql, spec_params)
            await session.execute(ev_sql, ev_params)
        else:
            async with self._db_factory() as db:
                await db.execute(spec_sql, spec_params)
                await db.execute(ev_sql, ev_params)
                await db.commit()

    async def update_evidence(
        self,
        skill_id: str,
        evidence: SkillEvidence,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        """Update evidence for an existing skill."""
        sql = text(f"""
            UPDATE {DB_SCHEMA}.skill_evidence SET
                source = :source,
                success_count = :success_count,
                failure_count = :failure_count,
                last_validated_at = :last_validated_at,
                positive_patterns = :positive_patterns,
                negative_patterns = :negative_patterns,
                known_breakages = :known_breakages,
                updated_at = now()
            WHERE skill_id = :skill_id
        """)
        params = {
            "skill_id": skill_id,
            "source": evidence.source,
            "success_count": evidence.success_count,
            "failure_count": evidence.failure_count,
            "last_validated_at": evidence.last_validated_at,
            "positive_patterns": list(evidence.positive_patterns),
            "negative_patterns": list(evidence.negative_patterns),
            "known_breakages": list(evidence.known_breakages),
        }
        if session is not None:
            await session.execute(sql, params)
        else:
            async with self._db_factory() as db:
                await db.execute(sql, params)
                await db.commit()

    async def get_by_id(self, skill_id: str) -> SkillSpec | None:
        """Return a single skill spec by ID, or None."""
        sql = text(
            f"SELECT {_SPEC_COLS} FROM {DB_SCHEMA}.skill_specs WHERE id = :skill_id"
        )
        async with self._db_factory() as db:
            result = await db.execute(sql, {"skill_id": skill_id})
            row = result.first()
            if row is None:
                return None
            return _row_to_spec(row)

    async def disable(
        self, skill_id: str, *, session: AsyncSession | None = None
    ) -> None:
        """Mark a skill as disabled (soft-delete)."""
        sql = text(
            f"UPDATE {DB_SCHEMA}.skill_specs SET disabled = true, updated_at = now() "
            "WHERE id = :skill_id"
        )
        if session is not None:
            await session.execute(sql, {"skill_id": skill_id})
        else:
            async with self._db_factory() as db:
                await db.execute(sql, {"skill_id": skill_id})
                await db.commit()

    # ── governance ledger helpers ──

    async def create_proposal(
        self,
        proposal: GrowthProposal,
        *,
        session: AsyncSession | None = None,
    ) -> int:
        """Insert a new governance ledger entry (status='proposed'). Returns governance_version."""
        sql = text(f"""
            INSERT INTO {DB_SCHEMA}.skill_spec_versions
                (skill_id, status, proposal, created_by)
            VALUES
                (:skill_id, 'proposed', :proposal, :created_by)
            RETURNING governance_version
        """)
        params = {
            "skill_id": proposal.object_id,
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

    async def get_proposal(self, governance_version: int) -> SkillProposalRecord | None:
        """Return a single governance ledger entry by governance_version."""
        sql = text(
            f"SELECT {_VERSION_COLS} FROM {DB_SCHEMA}.skill_spec_versions "
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
        sql = text(f"""
            UPDATE {DB_SCHEMA}.skill_spec_versions SET
                eval_result = :eval_result
            WHERE governance_version = :gv
        """)
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
            UPDATE {DB_SCHEMA}.skill_spec_versions SET
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

    # ── internal helpers used by the adapter ──

    async def find_last_applied(self, skill_id: str) -> SkillProposalRecord | None:
        """Find the most recent applied governance entry for a skill."""
        sql = text(
            f"SELECT {_VERSION_COLS} FROM {DB_SCHEMA}.skill_spec_versions "
            "WHERE skill_id = :skill_id AND status = 'active' "
            "ORDER BY governance_version DESC LIMIT 1"
        )
        async with self._db_factory() as db:
            result = await db.execute(sql, {"skill_id": skill_id})
            row = result.first()
            if row is None:
                return None
            return _row_to_proposal_record(row)
