"""Tests for EvolutionEngine (SOUL.md lifecycle).

Covers: propose, evaluate, apply, rollback, veto, bootstrap, audit trail,
superseded status, version conflict.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import MemorySettings
from src.memory.evolution import (
    EvalResult,
    EvolutionEngine,
    EvolutionError,
    SoulProposal,
    SoulVersion,
)
from src.memory.models import SoulVersionRecord


def _make_settings(workspace: Path) -> MemorySettings:
    return MemorySettings(
        workspace_path=workspace,
        max_daily_note_bytes=32_768,
        daily_notes_load_days=2,
        daily_notes_max_tokens=4000,
        flush_min_confidence=0.5,
        curated_max_tokens=4000,
    )


def _make_proposal(content: str = "# Soul\nI am Magi.") -> SoulProposal:
    return SoulProposal(
        intent="Update identity",
        risk_notes="None",
        diff_summary="Changed identity text",
        new_content=content,
    )


# ── Mock DB helpers ──

def _make_mock_record(
    *,
    version: int = 1,
    content: str = "# Soul\nI am Magi.",
    status: str = "proposed",
    proposal: dict | None = None,
    eval_result: dict | None = None,
    created_by: str = "agent",
) -> MagicMock:
    record = MagicMock(spec=SoulVersionRecord)
    record.id = version
    record.version = version
    record.content = content
    record.status = status
    record.proposal = proposal
    record.eval_result = eval_result
    record.created_by = created_by
    record.created_at = None
    return record


class TestSoulProposal:
    def test_create(self) -> None:
        p = _make_proposal()
        assert p.intent == "Update identity"
        assert p.new_content.startswith("# Soul")

    def test_frozen(self) -> None:
        p = _make_proposal()
        with pytest.raises(AttributeError):
            p.intent = "changed"


class TestEvalResult:
    def test_create(self) -> None:
        r = EvalResult(passed=True, summary="All checks passed")
        assert r.passed is True


class TestSoulVersion:
    def test_create(self) -> None:
        v = SoulVersion(
            id=1, version=1, content="test", status="active",
            proposal=None, eval_result=None, created_by="agent", created_at=None,
        )
        assert v.version == 1
        assert v.status == "active"


class TestEvolutionErrorClass:
    def test_error_code(self) -> None:
        err = EvolutionError("test error", code="TEST_CODE")
        assert "test error" in str(err)


class TestEvaluateChecks:
    """Test eval check logic with mocked DB."""

    @pytest.mark.asyncio
    async def test_empty_content_fails(self, tmp_path: Path) -> None:
        """Empty content should fail content_coherence check."""
        proposed = _make_mock_record(content="", status="proposed")

        # Mock DB
        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.first.side_effect = [proposed, None]  # get_version, get_active
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_result.scalar.return_value = 1
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        factory = MagicMock(return_value=mock_db)
        settings = _make_settings(tmp_path)
        engine = EvolutionEngine(factory, tmp_path, settings)

        result = await engine.evaluate(1)
        assert result.passed is False
        assert any(c.name == "content_coherence" and not c.passed for c in result.checks)

    @pytest.mark.asyncio
    async def test_wrong_status_fails(self, tmp_path: Path) -> None:
        """Non-proposed status should fail eval."""
        active = _make_mock_record(status="active")

        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = active
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        factory = MagicMock(return_value=mock_db)
        engine = EvolutionEngine(factory, tmp_path)

        result = await engine.evaluate(1)
        assert result.passed is False
        assert "active" in result.summary


class TestApplyChecks:
    @pytest.mark.asyncio
    async def test_apply_non_proposed_raises(self, tmp_path: Path) -> None:
        """Cannot apply a version that isn't 'proposed'."""
        active = _make_mock_record(status="active")

        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = active
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        factory = MagicMock(return_value=mock_db)
        engine = EvolutionEngine(factory, tmp_path)

        with pytest.raises(EvolutionError, match="status is 'active'"):
            await engine.apply(1)

    @pytest.mark.asyncio
    async def test_apply_no_eval_raises(self, tmp_path: Path) -> None:
        """Cannot apply if eval not passed."""
        proposed = _make_mock_record(status="proposed", eval_result=None)

        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = proposed
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        factory = MagicMock(return_value=mock_db)
        engine = EvolutionEngine(factory, tmp_path)

        with pytest.raises(EvolutionError, match="eval not passed"):
            await engine.apply(1)


class TestRollbackChecks:
    @pytest.mark.asyncio
    async def test_no_target_raises(self, tmp_path: Path) -> None:
        """Rollback with no superseded version raises error."""
        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = None
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        factory = MagicMock(return_value=mock_db)
        engine = EvolutionEngine(factory, tmp_path)

        with pytest.raises(EvolutionError, match="No previous version to rollback to"):
            await engine.rollback()


class TestBootstrap:
    @pytest.mark.asyncio
    async def test_bootstrap_no_file(self, tmp_path: Path) -> None:
        """No SOUL.md → no bootstrap action."""
        factory = MagicMock()
        engine = EvolutionEngine(factory, tmp_path)

        await engine.ensure_bootstrap(tmp_path)
        # No DB calls should happen
        factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_bootstrap_already_exists(self, tmp_path: Path) -> None:
        """SOUL.md exists + DB version exists → skip."""
        (tmp_path / "SOUL.md").write_text("# Existing Soul")

        engine = EvolutionEngine(MagicMock(), tmp_path)
        engine.get_current_version = AsyncMock(
            return_value=SoulVersion(
                id=1, version=0, content="existing", status="active",
                proposal=None, eval_result=None, created_by="bootstrap",
                created_at=None,
            )
        )

        await engine.ensure_bootstrap(tmp_path)
        # Should not try to write to DB

    @pytest.mark.asyncio
    async def test_bootstrap_imports_file(self, tmp_path: Path) -> None:
        """SOUL.md exists + no DB version → imports as v0-seed."""
        (tmp_path / "SOUL.md").write_text("# My Soul\nI am Magi.")

        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        factory = MagicMock(return_value=mock_db)
        engine = EvolutionEngine(factory, tmp_path)
        engine.get_current_version = AsyncMock(return_value=None)

        await engine.ensure_bootstrap(tmp_path)

        # Should have added a record
        mock_db.add.assert_called_once()
        added = mock_db.add.call_args[0][0]
        assert added.version == 0
        assert added.status == "active"
        assert added.created_by == "bootstrap"


class TestAuditTrail:
    @pytest.mark.asyncio
    async def test_returns_versions(self, tmp_path: Path) -> None:
        v1 = _make_mock_record(version=1, status="superseded")
        v2 = _make_mock_record(version=2, status="active")

        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [v2, v1]
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        factory = MagicMock(return_value=mock_db)
        engine = EvolutionEngine(factory, tmp_path)

        trail = await engine.get_audit_trail(limit=5)
        assert len(trail) == 2
        assert trail[0].version == 2
        assert trail[1].version == 1


class TestApplyCompensation:
    """ADR 0036: any DB failure after file write must compensate."""

    @pytest.mark.asyncio
    async def test_apply_commit_failure_compensates_file(self, tmp_path: Path) -> None:
        """apply() commit failure → old file content restored."""
        soul_path = tmp_path / "SOUL.md"
        old_content = "# Old Soul\nOriginal content."
        soul_path.write_text(old_content, encoding="utf-8")

        proposed = _make_mock_record(
            version=2,
            content="# New Soul\nUpdated content.",
            status="proposed",
            eval_result={"passed": True},
        )

        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = proposed
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock(side_effect=RuntimeError("DB commit failed"))
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        factory = MagicMock(return_value=mock_db)
        engine = EvolutionEngine(factory, tmp_path)

        with pytest.raises(RuntimeError, match="DB commit failed"):
            await engine.apply(2)

        # File should be restored to old content
        assert soul_path.read_text(encoding="utf-8") == old_content

    @pytest.mark.asyncio
    async def test_apply_execute_failure_compensates_file(self, tmp_path: Path) -> None:
        """apply() execute failure (pre-commit) → old file content restored."""
        soul_path = tmp_path / "SOUL.md"
        old_content = "# Old Soul\nOriginal."
        soul_path.write_text(old_content, encoding="utf-8")

        proposed = _make_mock_record(
            version=2,
            content="# New Soul\nNew.",
            status="proposed",
            eval_result={"passed": True},
        )

        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            # First call: _get_version (inside context manager)
            if call_count == 1:
                result = MagicMock()
                scalars = MagicMock()
                scalars.first.return_value = proposed
                result.scalars.return_value = scalars
                return result
            # Second call: supersede — raise to simulate DB execute failure
            raise RuntimeError("DB execute failed")

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(side_effect=execute_side_effect)
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        factory = MagicMock(return_value=mock_db)
        engine = EvolutionEngine(factory, tmp_path)

        with pytest.raises(RuntimeError, match="DB execute failed"):
            await engine.apply(2)

        # File should be restored to old content
        assert soul_path.read_text(encoding="utf-8") == old_content

    @pytest.mark.asyncio
    async def test_apply_compensation_failure_logs_and_raises(self, tmp_path: Path) -> None:
        """Compensation write failure → structured log + original error re-raised."""
        from unittest.mock import patch as _patch

        soul_path = tmp_path / "SOUL.md"
        soul_path.write_text("# Old", encoding="utf-8")

        proposed = _make_mock_record(
            version=2,
            content="# New",
            status="proposed",
            eval_result={"passed": True},
        )

        mock_db = AsyncMock()
        mock_scalars = MagicMock()
        mock_scalars.first.return_value = proposed
        mock_result = MagicMock()
        mock_result.scalars.return_value = mock_scalars
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock(side_effect=RuntimeError("DB commit failed"))
        mock_db.__aenter__ = AsyncMock(return_value=mock_db)
        mock_db.__aexit__ = AsyncMock(return_value=False)

        factory = MagicMock(return_value=mock_db)
        engine = EvolutionEngine(factory, tmp_path)

        # Make compensation write_text fail by patching Path.write_text
        # to raise on the second call (first call writes new content, second is compensation)
        original_write = Path.write_text
        call_count = 0

        def write_text_side_effect(self_path, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise OSError("Disk full")
            return original_write(self_path, *args, **kwargs)

        with _patch.object(Path, "write_text", write_text_side_effect):
            # Original DB error should still be raised despite compensation failure
            with pytest.raises(RuntimeError, match="DB commit failed"):
                await engine.apply(2)


class TestReconcileSoulProjection:
    """ADR 0036: startup reconciliation — DB is SSOT."""

    @pytest.mark.asyncio
    async def test_reconcile_soul_projection_fixes_drift(self, tmp_path: Path) -> None:
        """File content differs from DB → file overwritten with DB content."""
        soul_path = tmp_path / "SOUL.md"
        soul_path.write_text("# Drifted content", encoding="utf-8")

        db_content = "# Soul from DB\nDB is SSOT."
        engine = EvolutionEngine(MagicMock(), tmp_path)
        engine.get_current_version = AsyncMock(
            return_value=SoulVersion(
                id=1, version=1, content=db_content, status="active",
                proposal=None, eval_result=None, created_by="agent",
                created_at=None,
            )
        )

        await engine.reconcile_soul_projection()

        assert soul_path.read_text(encoding="utf-8") == db_content

    @pytest.mark.asyncio
    async def test_reconcile_no_active_skips(self, tmp_path: Path) -> None:
        """No active version in DB → skip reconciliation."""
        soul_path = tmp_path / "SOUL.md"
        soul_path.write_text("# Whatever", encoding="utf-8")

        engine = EvolutionEngine(MagicMock(), tmp_path)
        engine.get_current_version = AsyncMock(return_value=None)

        await engine.reconcile_soul_projection()

        # File unchanged
        assert soul_path.read_text(encoding="utf-8") == "# Whatever"
