"""Tests for scripts/restore.py — restore script unit tests.

Verifies the 8-step recovery sequence executes in correct order.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.health import CheckResult, CheckStatus, PreflightReport


class TestRestorePgRestoreCheck:
    def test_pg_restore_not_found_exits(self) -> None:
        from scripts.restore import _check_pg_restore

        with patch("shutil.which", return_value=None):
            with pytest.raises(SystemExit) as exc_info:
                _check_pg_restore()
            assert exc_info.value.code == 1

    def test_pg_restore_found_returns_path(self) -> None:
        from scripts.restore import _check_pg_restore

        with patch("shutil.which", return_value="/usr/bin/pg_restore"):
            result = _check_pg_restore()
            assert result == "/usr/bin/pg_restore"


def _make_restore_patches(
    tmp_path: Path,
    *,
    execution_log: list[str] | None = None,
    preflight_report: PreflightReport | None = None,
    reindex_count: int = 42,
):
    """Build context manager patches for restore tests.

    Returns a dict of (name -> patch_context_manager).
    Patches are at source-module level because restore.py uses deferred imports.
    """
    if execution_log is None:
        execution_log = []

    mock_check = patch(
        "scripts.restore._check_pg_restore", return_value="/usr/bin/pg_restore"
    )
    mock_dsn = patch(
        "scripts.restore._get_dsn",
        return_value="postgresql://user@localhost/neomagi",
    )

    def track_subprocess(cmd, **kwargs):
        if "pg_restore" in str(cmd[0]):
            execution_log.append("step2_pg_restore")
        elif "tar" in str(cmd[0]):
            execution_log.append("step4_tar_extract")
        return MagicMock(returncode=0, stderr="", stdout="")

    mock_subprocess = patch("scripts.restore.subprocess.run", side_effect=track_subprocess)

    mock_settings = MagicMock()
    mock_settings.database = MagicMock()
    mock_settings.memory.workspace_path = tmp_path / "workspace"
    mock_get_settings = patch(
        "src.config.settings.get_settings", return_value=mock_settings
    )

    mock_conn = AsyncMock()

    async def track_execute(stmt):
        stmt_str = str(stmt) if not hasattr(stmt, "text") else str(stmt.text)
        if "TRUNCATE" in stmt_str:
            execution_log.append("step6_truncate")
        return MagicMock(scalar=lambda: 0)

    mock_conn.execute = track_execute

    begin_cm = MagicMock()
    begin_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    begin_cm.__aexit__ = AsyncMock(return_value=None)

    mock_engine = MagicMock()
    mock_engine.begin = MagicMock(return_value=begin_cm)
    mock_engine.dispose = AsyncMock()
    mock_create_engine = patch(
        "src.session.database.create_db_engine", return_value=mock_engine
    )

    async def track_ensure_schema(*args, **kwargs):
        execution_log.append("step3_ensure_schema")

    mock_ensure = patch(
        "src.session.database.ensure_schema", side_effect=track_ensure_schema
    )

    mock_session_factory = MagicMock()
    mock_make_sf = patch(
        "src.session.database.make_session_factory", return_value=mock_session_factory
    )

    mock_evolution = AsyncMock()

    async def track_reconcile():
        execution_log.append("step5_reconcile")

    mock_evolution.reconcile_soul_projection = track_reconcile
    mock_evolution_cls = patch(
        "src.memory.evolution.EvolutionEngine", return_value=mock_evolution
    )

    mock_indexer = AsyncMock()

    async def track_reindex(**kwargs):
        execution_log.append("step7_reindex")
        return reindex_count

    mock_indexer.reindex_all = track_reindex
    mock_indexer_cls = patch(
        "src.memory.indexer.MemoryIndexer", return_value=mock_indexer
    )

    if preflight_report is None:
        preflight_report = PreflightReport(
            checks=[
                CheckResult(
                    name="test", status=CheckStatus.OK,
                    evidence="ok", impact="none", next_action="none",
                )
            ]
        )

    async def track_preflight(*args, **kwargs):
        execution_log.append("step8_preflight")
        return preflight_report

    mock_preflight = patch(
        "src.infra.preflight.run_preflight", side_effect=track_preflight
    )

    return (
        mock_check,
        mock_dsn,
        mock_subprocess,
        mock_get_settings,
        mock_create_engine,
        mock_ensure,
        mock_make_sf,
        mock_evolution_cls,
        mock_indexer_cls,
        mock_preflight,
    )


class TestRunRestore:
    @pytest.mark.asyncio
    async def test_8_step_sequence_order(self, tmp_path: Path) -> None:
        """Verify all 8 steps execute in correct order."""
        from scripts.restore import run_restore

        execution_log: list[str] = []
        patches = _make_restore_patches(tmp_path, execution_log=execution_log)

        db_dump = tmp_path / "test.dump"
        db_dump.write_bytes(b"fake dump")
        ws_archive = tmp_path / "test.tar.gz"
        ws_archive.write_bytes(b"fake archive")

        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9]:
            await run_restore(db_dump, ws_archive)

        assert execution_log == [
            "step2_pg_restore",
            "step3_ensure_schema",
            "step4_tar_extract",
            "step5_reconcile",
            "step6_truncate",
            "step7_reindex",
            "step8_preflight",
        ]

    @pytest.mark.asyncio
    async def test_preflight_fail_exits(self, tmp_path: Path) -> None:
        """If preflight reports FAIL, restore should exit 1."""
        from scripts.restore import run_restore

        fail_report = PreflightReport(
            checks=[
                CheckResult(
                    name="db_connection",
                    status=CheckStatus.FAIL,
                    evidence="connection refused",
                    impact="service cannot start",
                    next_action="check DB",
                )
            ]
        )

        execution_log: list[str] = []
        patches = _make_restore_patches(
            tmp_path, execution_log=execution_log, preflight_report=fail_report
        )

        db_dump = tmp_path / "test.dump"
        db_dump.write_bytes(b"fake")
        ws_archive = tmp_path / "test.tar.gz"
        ws_archive.write_bytes(b"fake")

        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9]:
            with pytest.raises(SystemExit) as exc_info:
                await run_restore(db_dump, ws_archive)
            assert exc_info.value.code == 1

    @pytest.mark.asyncio
    async def test_ensure_schema_before_reindex(self, tmp_path: Path) -> None:
        """ensure_schema must run after pg_restore but before reindex."""
        from scripts.restore import run_restore

        execution_log: list[str] = []
        patches = _make_restore_patches(tmp_path, execution_log=execution_log)

        db_dump = tmp_path / "test.dump"
        db_dump.write_bytes(b"fake")
        ws_archive = tmp_path / "test.tar.gz"
        ws_archive.write_bytes(b"fake")

        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9]:
            await run_restore(db_dump, ws_archive)

        ensure_idx = execution_log.index("step3_ensure_schema")
        reindex_idx = execution_log.index("step7_reindex")
        pg_restore_idx = execution_log.index("step2_pg_restore")
        truncate_idx = execution_log.index("step6_truncate")

        assert pg_restore_idx < ensure_idx < truncate_idx < reindex_idx


class TestRestoreCli:
    def test_restore_help(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/restore.py", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd="/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/worktrees/backend-m5",
        )
        assert result.returncode == 0
        assert "--db-dump" in result.stdout
        assert "--workspace-archive" in result.stdout

    def test_missing_db_dump_exits(self, tmp_path: Path) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "scripts/restore.py",
                "--db-dump",
                str(tmp_path / "nonexistent.dump"),
                "--workspace-archive",
                str(tmp_path / "nonexistent.tar.gz"),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            cwd="/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/worktrees/backend-m5",
        )
        assert result.returncode != 0
