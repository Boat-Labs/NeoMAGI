"""Evolution engine: SOUL.md lifecycle management.

Manages propose → eval → apply → rollback cycle for SOUL.md changes.
All mutations are auditable via soul_versions table (ADR 0027).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.infra.errors import NeoMAGIError
from src.memory.models import SoulVersionRecord

if TYPE_CHECKING:
    from src.config.settings import MemorySettings

logger = structlog.get_logger()

VALID_STATUSES = {"active", "proposed", "superseded", "rolled_back", "vetoed"}


class EvolutionError(NeoMAGIError):
    """Error in SOUL.md evolution lifecycle."""

    def __init__(self, message: str, *, code: str = "EVOLUTION_ERROR") -> None:
        super().__init__(message, code=code)


@dataclass(frozen=True)
class SoulProposal:
    """Proposed change to SOUL.md."""

    intent: str
    risk_notes: str
    diff_summary: str
    new_content: str
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class EvalCheck:
    """Single evaluation check result."""

    name: str
    passed: bool
    detail: str


@dataclass
class EvalResult:
    """Result of evaluating a proposed SOUL.md change."""

    passed: bool
    checks: list[EvalCheck] = field(default_factory=list)
    summary: str = ""


@dataclass
class SoulVersion:
    """SOUL.md version record (read model)."""

    id: int
    version: int
    content: str
    status: str
    proposal: dict | None
    eval_result: dict | None
    created_by: str
    created_at: object  # datetime


class EvolutionEngine:
    """Manages SOUL.md lifecycle: propose → eval → apply → rollback.

    Governance rules (ADR 0027):
    - Only agent can write SOUL.md content (post-bootstrap)
    - All changes must pass eval before taking effect
    - User retains veto/rollback at any time
    - Full audit trail in soul_versions table
    """

    def __init__(
        self,
        db_session_factory: async_sessionmaker[AsyncSession],
        workspace_path: Path,
        settings: MemorySettings | None = None,
    ) -> None:
        self._db_factory = db_session_factory
        self._workspace_path = workspace_path
        self._settings = settings

    async def get_current_version(self) -> SoulVersion | None:
        """Get the currently active SOUL.md version from DB."""
        async with self._db_factory() as db:
            result = await db.execute(
                select(SoulVersionRecord)
                .where(SoulVersionRecord.status == "active")
                .order_by(SoulVersionRecord.version.desc())
                .limit(1)
            )
            row = result.scalars().first()
            if not row:
                return None
            return self._to_soul_version(row)

    async def propose(self, proposal: SoulProposal) -> int:
        """Record a proposed change. Returns proposal version number.

        Status = 'proposed'. Does NOT apply the change.
        """
        async with self._db_factory() as db:
            next_version = await self._next_version(db)
            record = SoulVersionRecord(
                version=next_version,
                content=proposal.new_content,
                status="proposed",
                proposal={
                    "intent": proposal.intent,
                    "risk_notes": proposal.risk_notes,
                    "diff_summary": proposal.diff_summary,
                    "evidence_refs": proposal.evidence_refs,
                },
                eval_result=None,
                created_by="agent",
            )
            db.add(record)
            await db.commit()

        logger.info("soul_proposed", version=next_version, intent=proposal.intent[:50])
        return next_version

    async def evaluate(self, version: int) -> EvalResult:
        """Run eval checks against a proposed version.

        Checks:
        1. Content coherence: well-formed, non-empty
        2. Size limit: within configured max tokens
        3. Diff sanity: not identical to current active version
        """
        async with self._db_factory() as db:
            record = await self._get_version(db, version)
            if record.status != "proposed":
                return EvalResult(
                    passed=False,
                    summary=f"Version {version} is '{record.status}', not 'proposed'",
                )

            checks: list[EvalCheck] = []

            # Check 1: Content coherence
            content = record.content
            is_coherent = bool(content and content.strip())
            checks.append(EvalCheck(
                name="content_coherence",
                passed=is_coherent,
                detail="Content is non-empty and well-formed" if is_coherent
                else "Content is empty or whitespace-only",
            ))

            # Check 2: Size limit
            max_tokens = 4000
            if self._settings:
                max_tokens = self._settings.curated_max_tokens
            max_chars = max_tokens * 4
            within_limit = len(content) <= max_chars
            checks.append(EvalCheck(
                name="size_limit",
                passed=within_limit,
                detail=f"Content size {len(content)} chars"
                + (f" (limit: {max_chars})" if not within_limit else ""),
            ))

            # Check 3: Diff sanity — not identical to current
            current = await self._get_active_version(db)
            is_different = current is None or current.content.strip() != content.strip()
            checks.append(EvalCheck(
                name="diff_sanity",
                passed=is_different,
                detail="Content differs from current active version" if is_different
                else "Content identical to current active version",
            ))

            passed = all(c.passed for c in checks)
            summary = (
                "All checks passed" if passed
                else "Failed: " + ", ".join(c.name for c in checks if not c.passed)
            )

            # Store eval result
            eval_dict = {
                "passed": passed,
                "checks": [
                    {"name": c.name, "passed": c.passed, "detail": c.detail}
                    for c in checks
                ],
                "summary": summary,
            }
            await db.execute(
                update(SoulVersionRecord)
                .where(SoulVersionRecord.version == version)
                .values(eval_result=eval_dict)
            )
            await db.commit()

        logger.info("soul_evaluated", version=version, passed=passed)
        return EvalResult(passed=passed, checks=checks, summary=summary)

    async def apply(self, version: int) -> None:
        """Apply a proposed version that passed eval (ADR 0036 compensation).

        1. Verify status == 'proposed' and eval passed
        2. Save old SOUL.md content (for compensation)
        3. Write new content to workspace/SOUL.md
        4. Update DB: new version → 'active', old active → 'superseded'
        5. On commit failure: restore old file content + raise
        """
        async with self._db_factory() as db:
            record = await self._get_version(db, version)

            if record.status != "proposed":
                raise EvolutionError(
                    f"Cannot apply version {version}: status is '{record.status}'",
                    code="INVALID_STATUS",
                )
            if not record.eval_result or not record.eval_result.get("passed"):
                raise EvolutionError(
                    f"Cannot apply version {version}: eval not passed",
                    code="EVAL_NOT_PASSED",
                )

            # ADR 0036: save old content for compensation
            soul_path = self._workspace_path / "SOUL.md"
            old_content: str | None = None
            if soul_path.is_file():
                old_content = soul_path.read_text(encoding="utf-8")

            # Write new content to file BEFORE commit
            soul_path.write_text(record.content, encoding="utf-8")

            # Supersede current active version
            await db.execute(
                update(SoulVersionRecord)
                .where(SoulVersionRecord.status == "active")
                .values(status="superseded")
            )

            # Activate proposed version
            await db.execute(
                update(SoulVersionRecord)
                .where(SoulVersionRecord.version == version)
                .values(status="active")
            )

            try:
                await db.commit()
            except Exception:
                # ADR 0036: compensate — restore old file content
                logger.error(
                    "soul_apply_commit_failed",
                    version=version,
                    msg="Commit failed, compensating file write",
                )
                if old_content is not None:
                    soul_path.write_text(old_content, encoding="utf-8")
                else:
                    soul_path.unlink(missing_ok=True)
                raise

        logger.info("soul_applied", version=version)

    async def rollback(self, *, to_version: int | None = None) -> int:
        """Rollback to a previous version (ADR 0036 compensation).

        If to_version is None, rolls back to the most recent superseded version.
        Returns the new active version number.
        """
        async with self._db_factory() as db:
            # Find target
            if to_version is not None:
                target = await self._get_version(db, to_version)
            else:
                result = await db.execute(
                    select(SoulVersionRecord)
                    .where(SoulVersionRecord.status == "superseded")
                    .order_by(SoulVersionRecord.version.desc())
                    .limit(1)
                )
                target = result.scalars().first()
                if not target:
                    raise EvolutionError(
                        "No previous version to rollback to",
                        code="NO_ROLLBACK_TARGET",
                    )

            # ADR 0036: save old content for compensation
            soul_path = self._workspace_path / "SOUL.md"
            old_content: str | None = None
            if soul_path.is_file():
                old_content = soul_path.read_text(encoding="utf-8")

            # Write target content to file BEFORE commit
            soul_path.write_text(target.content, encoding="utf-8")

            # Mark current active as rolled_back
            await db.execute(
                update(SoulVersionRecord)
                .where(SoulVersionRecord.status == "active")
                .values(status="rolled_back")
            )

            # Create new version with target content
            next_version = await self._next_version(db)
            new_record = SoulVersionRecord(
                version=next_version,
                content=target.content,
                status="active",
                proposal={"rollback_from": target.version},
                eval_result=None,
                created_by="system",
            )
            db.add(new_record)

            try:
                await db.commit()
            except Exception:
                # ADR 0036: compensate — restore old file content
                logger.error(
                    "soul_rollback_commit_failed",
                    target_version=target.version,
                    msg="Commit failed, compensating file write",
                )
                if old_content is not None:
                    soul_path.write_text(old_content, encoding="utf-8")
                else:
                    soul_path.unlink(missing_ok=True)
                raise

        logger.info("soul_rolled_back", new_version=next_version, target=target.version)
        return next_version

    async def veto(self, version: int) -> None:
        """User vetoes a proposed or active version.

        If active → triggers rollback. If proposed → marks as 'vetoed'.
        """
        async with self._db_factory() as db:
            record = await self._get_version(db, version)

            if record.status == "proposed":
                await db.execute(
                    update(SoulVersionRecord)
                    .where(SoulVersionRecord.version == version)
                    .values(status="vetoed")
                )
                await db.commit()
                logger.info("soul_vetoed", version=version, was_status="proposed")
            elif record.status == "active":
                # Veto active → rollback
                await db.commit()  # release current transaction
                await self.rollback()
                logger.info("soul_vetoed", version=version, was_status="active")
            else:
                raise EvolutionError(
                    f"Cannot veto version {version}: status is '{record.status}'",
                    code="INVALID_VETO_TARGET",
                )

    async def get_audit_trail(self, *, limit: int = 20) -> list[SoulVersion]:
        """Get version history for audit/review."""
        async with self._db_factory() as db:
            result = await db.execute(
                select(SoulVersionRecord)
                .order_by(SoulVersionRecord.version.desc())
                .limit(limit)
            )
            return [self._to_soul_version(r) for r in result.scalars().all()]

    async def reconcile_soul_projection(self) -> None:
        """ADR 0036: startup reconciliation — DB is SSOT, SOUL.md is projection.

        If DB active version content differs from SOUL.md, overwrite file with DB content.
        If no active version exists, skip (bootstrap handles that case).
        """
        current = await self.get_current_version()
        if current is None:
            logger.info("soul_reconcile_skipped", reason="no_active_version")
            return

        soul_path = self._workspace_path / "SOUL.md"
        file_content = ""
        if soul_path.is_file():
            file_content = soul_path.read_text(encoding="utf-8")

        if file_content.strip() == current.content.strip():
            return  # Already consistent

        # DB is SSOT — overwrite file
        soul_path.write_text(current.content, encoding="utf-8")
        logger.warning(
            "soul_projection_reconciled",
            version=current.version,
            msg="SOUL.md overwritten to match DB active version",
        )

    async def ensure_bootstrap(self, workspace_path: Path) -> None:
        """Handle SOUL.md bootstrap (ADR 0027).

        If SOUL.md exists in workspace but no DB version → import as v0-seed.
        """
        soul_path = workspace_path / "SOUL.md"
        if not soul_path.is_file():
            return

        current = await self.get_current_version()
        if current is not None:
            return  # Already bootstrapped

        content = soul_path.read_text(encoding="utf-8").strip()
        if not content:
            return

        async with self._db_factory() as db:
            record = SoulVersionRecord(
                version=0,
                content=content,
                status="active",
                proposal={"bootstrap": True, "source": "file"},
                eval_result=None,
                created_by="bootstrap",
            )
            db.add(record)
            await db.commit()

        logger.info("soul_bootstrapped", version=0, chars=len(content))

    # ── helpers ──

    @staticmethod
    async def _next_version(db: AsyncSession) -> int:
        """Get next monotonic version number."""
        result = await db.execute(
            select(SoulVersionRecord.version)
            .order_by(SoulVersionRecord.version.desc())
            .limit(1)
        )
        max_ver = result.scalar()
        return (max_ver or 0) + 1

    @staticmethod
    async def _get_version(db: AsyncSession, version: int) -> SoulVersionRecord:
        """Get a specific version record. Raises if not found."""
        result = await db.execute(
            select(SoulVersionRecord).where(SoulVersionRecord.version == version)
        )
        record = result.scalars().first()
        if not record:
            raise EvolutionError(
                f"Version {version} not found", code="VERSION_NOT_FOUND"
            )
        return record

    @staticmethod
    async def _get_active_version(db: AsyncSession) -> SoulVersionRecord | None:
        """Get the currently active version record."""
        result = await db.execute(
            select(SoulVersionRecord)
            .where(SoulVersionRecord.status == "active")
            .order_by(SoulVersionRecord.version.desc())
            .limit(1)
        )
        return result.scalars().first()

    @staticmethod
    def _to_soul_version(record: SoulVersionRecord) -> SoulVersion:
        """Convert DB record to read model."""
        return SoulVersion(
            id=record.id,
            version=record.version,
            content=record.content,
            status=record.status,
            proposal=record.proposal,
            eval_result=record.eval_result,
            created_by=record.created_by,
            created_at=record.created_at,
        )
