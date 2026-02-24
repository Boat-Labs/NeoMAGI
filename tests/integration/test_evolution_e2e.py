"""End-to-end integration tests for EvolutionEngine against real PostgreSQL.

Covers the full lifecycle: bootstrap → propose → eval → apply → rollback → veto → audit.
Gate conditions: G-M3-P4 #1–#9.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config.settings import MemorySettings
from src.constants import DB_SCHEMA
from src.memory.evolution import EvolutionEngine, EvolutionError, SoulProposal


def _make_settings(workspace: Path) -> MemorySettings:
    return MemorySettings(
        workspace_path=workspace,
        max_daily_note_bytes=32_768,
        daily_notes_load_days=2,
        daily_notes_max_tokens=4000,
        flush_min_confidence=0.5,
        curated_max_tokens=4000,
    )


def _make_proposal(content: str = "# Soul\nI am Magi.", intent: str = "Update identity") -> SoulProposal:
    return SoulProposal(
        intent=intent,
        risk_notes="None",
        diff_summary="Changed identity text",
        new_content=content,
    )


@pytest_asyncio.fixture()
async def evolution_db(db_engine, db_session_factory):
    """Ensure soul_versions table exists and is clean."""
    async with db_engine.begin() as conn:
        await conn.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {DB_SCHEMA}.soul_versions ("
                "id SERIAL PRIMARY KEY,"
                "version INTEGER NOT NULL,"
                "content TEXT NOT NULL,"
                "status VARCHAR(16) NOT NULL,"
                "proposal JSONB,"
                "eval_result JSONB,"
                "created_by VARCHAR(32) NOT NULL,"
                "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),"
                "CONSTRAINT uq_soul_versions_version UNIQUE (version))"
            )
        )
    yield db_session_factory
    async with db_session_factory() as db:
        await db.execute(text(f"TRUNCATE {DB_SCHEMA}.soul_versions CASCADE"))
        await db.commit()


@pytest.mark.integration
class TestBootstrapE2E:
    """Gate condition #6: bootstrap protocol."""

    @pytest.mark.asyncio
    async def test_bootstrap_imports_soul_file(
        self, evolution_db: async_sessionmaker[AsyncSession], tmp_path: Path
    ) -> None:
        """SOUL.md exists + no DB version → import as v0-seed."""
        (tmp_path / "SOUL.md").write_text("# My Soul\nI am the original soul.")
        settings = _make_settings(tmp_path)
        engine = EvolutionEngine(evolution_db, tmp_path, settings)

        await engine.ensure_bootstrap(tmp_path)

        current = await engine.get_current_version()
        assert current is not None
        assert current.version == 0
        assert current.status == "active"
        assert current.created_by == "bootstrap"
        assert "original soul" in current.content

    @pytest.mark.asyncio
    async def test_bootstrap_idempotent(
        self, evolution_db: async_sessionmaker[AsyncSession], tmp_path: Path
    ) -> None:
        """Second bootstrap call does nothing."""
        (tmp_path / "SOUL.md").write_text("# Soul")
        engine = EvolutionEngine(evolution_db, tmp_path)

        await engine.ensure_bootstrap(tmp_path)
        await engine.ensure_bootstrap(tmp_path)

        trail = await engine.get_audit_trail(limit=10)
        assert len(trail) == 1  # Only one v0


@pytest.mark.integration
class TestProposeEvalApplyE2E:
    """Gate conditions #2, #3, #8 (Use Case C)."""

    @pytest.mark.asyncio
    async def test_full_pass_chain(
        self, evolution_db: async_sessionmaker[AsyncSession], tmp_path: Path
    ) -> None:
        """propose → eval(pass) → apply → file written + DB active."""
        settings = _make_settings(tmp_path)
        engine = EvolutionEngine(evolution_db, tmp_path, settings)

        proposal = _make_proposal("# Soul v1\nI have evolved.")
        version = await engine.propose(proposal)
        assert version == 1

        eval_result = await engine.evaluate(version)
        assert eval_result.passed is True
        assert all(c.passed for c in eval_result.checks)

        await engine.apply(version)

        # Verify DB
        current = await engine.get_current_version()
        assert current is not None
        assert current.version == 1
        assert current.status == "active"
        assert "evolved" in current.content

        # Verify file
        soul_path = tmp_path / "SOUL.md"
        assert soul_path.exists()
        assert "evolved" in soul_path.read_text()

    @pytest.mark.asyncio
    async def test_eval_fail_rejects(
        self, evolution_db: async_sessionmaker[AsyncSession], tmp_path: Path
    ) -> None:
        """propose → eval(fail: empty content) → cannot apply."""
        settings = _make_settings(tmp_path)
        engine = EvolutionEngine(evolution_db, tmp_path, settings)

        proposal = _make_proposal(content="")
        version = await engine.propose(proposal)

        eval_result = await engine.evaluate(version)
        assert eval_result.passed is False
        assert any(c.name == "content_coherence" and not c.passed for c in eval_result.checks)

        # Cannot apply
        with pytest.raises(EvolutionError, match="eval not passed"):
            await engine.apply(version)

    @pytest.mark.asyncio
    async def test_supersedes_old_version(
        self, evolution_db: async_sessionmaker[AsyncSession], tmp_path: Path
    ) -> None:
        """Second apply supersedes the first active version."""
        settings = _make_settings(tmp_path)
        engine = EvolutionEngine(evolution_db, tmp_path, settings)

        # v1
        v1 = await engine.propose(_make_proposal("# Soul v1\nFirst version."))
        await engine.evaluate(v1)
        await engine.apply(v1)

        # v2
        v2 = await engine.propose(_make_proposal("# Soul v2\nSecond version."))
        await engine.evaluate(v2)
        await engine.apply(v2)

        current = await engine.get_current_version()
        assert current.version == 2
        assert "Second version" in current.content

        # v1 should be superseded in trail
        trail = await engine.get_audit_trail(limit=10)
        v1_record = next(v for v in trail if v.version == 1)
        assert v1_record.status == "superseded"


@pytest.mark.integration
class TestRollbackE2E:
    """Gate condition #4: rollback restores version + file content."""

    @pytest.mark.asyncio
    async def test_rollback_restores_previous(
        self, evolution_db: async_sessionmaker[AsyncSession], tmp_path: Path
    ) -> None:
        """After apply v1 → apply v2, rollback restores v1 content."""
        settings = _make_settings(tmp_path)
        engine = EvolutionEngine(evolution_db, tmp_path, settings)

        # Setup: v1 active, then v2 active
        v1 = await engine.propose(_make_proposal("# Soul v1\nOriginal content."))
        await engine.evaluate(v1)
        await engine.apply(v1)

        v2 = await engine.propose(_make_proposal("# Soul v2\nNew content."))
        await engine.evaluate(v2)
        await engine.apply(v2)

        # Rollback
        new_ver = await engine.rollback()

        current = await engine.get_current_version()
        assert current.version == new_ver
        assert "Original content" in current.content

        # File should reflect rolled-back content
        soul_text = (tmp_path / "SOUL.md").read_text()
        assert "Original content" in soul_text


@pytest.mark.integration
class TestVetoE2E:
    """Gate condition #5: veto behavior."""

    @pytest.mark.asyncio
    async def test_veto_proposed_version(
        self, evolution_db: async_sessionmaker[AsyncSession], tmp_path: Path
    ) -> None:
        """Veto a proposed version → marked as 'vetoed'."""
        engine = EvolutionEngine(evolution_db, tmp_path)

        v1 = await engine.propose(_make_proposal("# Soul\nProposed."))
        await engine.veto(v1)

        trail = await engine.get_audit_trail(limit=10)
        vetoed = next(v for v in trail if v.version == v1)
        assert vetoed.status == "vetoed"

    @pytest.mark.asyncio
    async def test_veto_active_triggers_rollback(
        self, evolution_db: async_sessionmaker[AsyncSession], tmp_path: Path
    ) -> None:
        """Veto an active version → rollback to previous."""
        settings = _make_settings(tmp_path)
        engine = EvolutionEngine(evolution_db, tmp_path, settings)

        # v1 active
        v1 = await engine.propose(_make_proposal("# Soul v1\nBase content."))
        await engine.evaluate(v1)
        await engine.apply(v1)

        # v2 active (supersedes v1)
        v2 = await engine.propose(_make_proposal("# Soul v2\nBad content."))
        await engine.evaluate(v2)
        await engine.apply(v2)

        # Veto v2 → should rollback to v1 content
        await engine.veto(v2)

        current = await engine.get_current_version()
        assert current is not None
        assert "Base content" in current.content

        # File should reflect v1 content
        soul_text = (tmp_path / "SOUL.md").read_text()
        assert "Base content" in soul_text


@pytest.mark.integration
class TestAuditTrailE2E:
    """Gate conditions #7, #9 (Use Case D)."""

    @pytest.mark.asyncio
    async def test_full_audit_trail(
        self, evolution_db: async_sessionmaker[AsyncSession], tmp_path: Path
    ) -> None:
        """Full lifecycle produces traceable audit trail."""
        settings = _make_settings(tmp_path)
        engine = EvolutionEngine(evolution_db, tmp_path, settings)

        # Bootstrap
        (tmp_path / "SOUL.md").write_text("# Seed Soul")
        await engine.ensure_bootstrap(tmp_path)

        # Propose + apply
        v1 = await engine.propose(_make_proposal("# Soul v1\nEvolved."))
        await engine.evaluate(v1)
        await engine.apply(v1)

        # Rollback
        await engine.rollback()

        trail = await engine.get_audit_trail(limit=20)
        statuses = [(v.version, v.status) for v in trail]

        # Should have: v0(superseded), v1(superseded→rolled_back or similar), rollback version(active)
        assert len(trail) >= 3
        # Latest should be active
        assert trail[0].status == "active"
        # Should have mixed statuses
        all_statuses = {v.status for v in trail}
        assert "active" in all_statuses
        assert "superseded" in all_statuses or "rolled_back" in all_statuses
