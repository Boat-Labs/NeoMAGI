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


class TestPgRestoreErrorClassification:
    def test_transaction_timeout_is_ignorable(self) -> None:
        from scripts.restore import _split_pg_restore_errors

        stderr = (
            'pg_restore: error: could not execute query: ERROR:  unrecognized '
            'configuration parameter "transaction_timeout"\n'
            "Command was: SET transaction_timeout = 0;\n"
            "pg_restore: warning: errors ignored on restore: 1"
        )

        fatal, ignorable = _split_pg_restore_errors(stderr)
        assert fatal == []
        assert len(ignorable) == 1

    def test_non_ignorable_error_is_fatal(self) -> None:
        from scripts.restore import _split_pg_restore_errors

        stderr = "pg_restore: error: relation neomagi.sessions does not exist"
        fatal, ignorable = _split_pg_restore_errors(stderr)
        assert len(fatal) == 1
        assert ignorable == []


def _make_restore_patches(
    tmp_path: Path,
    *,
    execution_log: list[str] | None = None,
    preflight_report: PreflightReport | None = None,
    reindex_count: int = 42,
):
    """Build context manager patches for restore tests."""
    if execution_log is None:
        execution_log = []

    return (
        *_make_cli_patches(execution_log),
        _make_settings_patch(tmp_path),
        *_make_db_patches(execution_log),
        *_make_service_patches(execution_log, reindex_count),
        _make_preflight_patch(execution_log, preflight_report),
    )


def _make_cli_patches(execution_log: list[str]):
    """Patches for CLI-level dependencies (pg_restore, DSN, subprocess)."""
    def track_subprocess(cmd, **kwargs):
        if "pg_restore" in str(cmd[0]):
            execution_log.append("step2_pg_restore")
        elif "tar" in str(cmd[0]):
            execution_log.append("step4_tar_extract")
        return MagicMock(returncode=0, stderr="", stdout="")

    return (
        patch("scripts.restore._check_pg_restore", return_value="/usr/bin/pg_restore"),
        patch("scripts.restore._get_dsn", return_value="postgresql://user@localhost/neomagi"),
        patch("scripts.restore.subprocess.run", side_effect=track_subprocess),
    )


def _make_settings_patch(tmp_path: Path):
    """Patch for get_settings with a workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    mock_settings = MagicMock()
    mock_settings.database = MagicMock()
    mock_settings.workspace_dir = workspace
    mock_settings.memory.workspace_path = workspace
    return patch("src.config.settings.get_settings", return_value=mock_settings)


def _make_db_patches(execution_log: list[str]):
    """Patches for DB engine, ensure_schema, session_factory."""
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

    async def track_ensure_schema(*args, **kwargs):
        execution_log.append("step3_ensure_schema")

    return (
        patch("src.session.database.create_db_engine", return_value=mock_engine),
        patch("src.session.database.ensure_schema", side_effect=track_ensure_schema),
        patch("src.session.database.make_session_factory", return_value=MagicMock()),
    )


def _make_service_patches(execution_log: list[str], reindex_count: int):
    """Patches for EvolutionEngine and MemoryIndexer."""
    mock_evolution = AsyncMock()

    async def track_reconcile():
        execution_log.append("step5_reconcile")

    mock_evolution.reconcile_soul_projection = track_reconcile

    mock_indexer = AsyncMock()

    async def track_reindex(**kwargs):
        execution_log.append("step7_reindex")
        return reindex_count

    mock_indexer.reindex_all = track_reindex

    return (
        patch("src.memory.evolution.EvolutionEngine", return_value=mock_evolution),
        patch("src.memory.indexer.MemoryIndexer", return_value=mock_indexer),
    )


def _make_preflight_patch(execution_log: list[str], preflight_report=None):
    """Patch for run_preflight."""
    if preflight_report is None:
        preflight_report = PreflightReport(
            checks=[CheckResult(
                name="test", status=CheckStatus.OK,
                evidence="ok", impact="none", next_action="none",
            )]
        )

    async def track_preflight(*args, **kwargs):
        execution_log.append("step8_preflight")
        return preflight_report

    return patch("src.infra.preflight.run_preflight", side_effect=track_preflight)


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
        # Step 3.5 (clear workspace memory) is real file I/O, not mocked —
        # tested separately in test_clear_workspace_before_extract.

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

    @pytest.mark.asyncio
    async def test_clear_workspace_before_extract(self, tmp_path: Path) -> None:
        """Step 3.5: residual memory files must be cleared before tar extract."""
        from scripts.restore import run_restore

        execution_log: list[str] = []
        patches = _make_restore_patches(tmp_path, execution_log=execution_log)

        # Pre-create residual files in workspace
        workspace = tmp_path / "workspace"
        workspace.mkdir(exist_ok=True)
        mem_dir = workspace / "memory"
        mem_dir.mkdir()
        (mem_dir / "old-note.md").write_text("residual data")
        (workspace / "MEMORY.md").write_text("old curated memory")
        # Also create a non-memory file that should NOT be deleted
        (workspace / "SOUL.md").write_text("soul content")

        db_dump = tmp_path / "test.dump"
        db_dump.write_bytes(b"fake")
        ws_archive = tmp_path / "test.tar.gz"
        ws_archive.write_bytes(b"fake")

        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9]:
            await run_restore(db_dump, ws_archive)

        # memory/ dir and MEMORY.md should be cleared (by step 3.5)
        # tar mock doesn't recreate them, so they should be gone
        assert not mem_dir.exists()
        assert not (workspace / "MEMORY.md").exists()
        # SOUL.md should be preserved
        assert (workspace / "SOUL.md").exists()

    @pytest.mark.asyncio
    async def test_tar_uses_workspace_dir(self, tmp_path: Path) -> None:
        """Step 4 tar must use -C <workspace_dir> from settings."""
        from scripts.restore import run_restore

        execution_log: list[str] = []
        tar_commands: list[list] = []

        # Override the patches to capture the exact tar command
        patches = _make_restore_patches(tmp_path, execution_log=execution_log)

        def track_subprocess_with_tar(cmd, **kwargs):
            if "tar" in str(cmd[0]):
                tar_commands.append(list(cmd))
                execution_log.append("step4_tar_extract")
            elif "pg_restore" in str(cmd[0]):
                execution_log.append("step2_pg_restore")
            return MagicMock(returncode=0, stderr="", stdout="")

        mock_subprocess_override = patch(
            "scripts.restore.subprocess.run", side_effect=track_subprocess_with_tar
        )

        db_dump = tmp_path / "test.dump"
        db_dump.write_bytes(b"fake")
        ws_archive = tmp_path / "test.tar.gz"
        ws_archive.write_bytes(b"fake")

        with patches[0], patches[1], mock_subprocess_override, patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9]:
            await run_restore(db_dump, ws_archive)

        assert len(tar_commands) == 1
        tar_cmd = tar_commands[0]
        assert "-C" in tar_cmd
        c_idx = tar_cmd.index("-C")
        assert tar_cmd[c_idx + 1] == str((tmp_path / "workspace").resolve())

    @pytest.mark.asyncio
    async def test_pg_restore_transaction_timeout_warning_does_not_abort(
        self, tmp_path: Path
    ) -> None:
        """Known compatibility warning should not fail restore."""
        from scripts.restore import run_restore

        execution_log: list[str] = []
        patches = _make_restore_patches(tmp_path, execution_log=execution_log)

        compat_stderr = (
            'pg_restore: error: could not execute query: ERROR:  unrecognized '
            'configuration parameter "transaction_timeout"\n'
            "Command was: SET transaction_timeout = 0;\n"
            "pg_restore: warning: errors ignored on restore: 1"
        )

        def track_subprocess_with_known_warning(cmd, **kwargs):
            if "pg_restore" in str(cmd[0]):
                execution_log.append("step2_pg_restore")
                return MagicMock(returncode=1, stderr=compat_stderr, stdout="")
            if "tar" in str(cmd[0]):
                execution_log.append("step4_tar_extract")
                return MagicMock(returncode=0, stderr="", stdout="")
            return MagicMock(returncode=0, stderr="", stdout="")

        mock_subprocess_override = patch(
            "scripts.restore.subprocess.run", side_effect=track_subprocess_with_known_warning
        )

        db_dump = tmp_path / "test.dump"
        db_dump.write_bytes(b"fake")
        ws_archive = tmp_path / "test.tar.gz"
        ws_archive.write_bytes(b"fake")

        with patches[0], patches[1], mock_subprocess_override, patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], patches[9]:
            await run_restore(db_dump, ws_archive)

        assert "step8_preflight" in execution_log


class TestWorkspacePathGuard:
    def test_path_mismatch_exits(self) -> None:
        from scripts.restore import _assert_workspace_path_consistency

        mock_settings = MagicMock()
        mock_settings.workspace_dir = Path("/path/a")
        mock_settings.memory.workspace_path = Path("/path/b")
        with patch("src.config.settings.get_settings", return_value=mock_settings):
            with pytest.raises(SystemExit) as exc_info:
                _assert_workspace_path_consistency()
            assert exc_info.value.code == 1

    def test_path_match_returns_workspace(self, tmp_path: Path) -> None:
        from scripts.restore import _assert_workspace_path_consistency

        mock_settings = MagicMock()
        mock_settings.workspace_dir = tmp_path
        mock_settings.memory.workspace_path = tmp_path
        with patch("src.config.settings.get_settings", return_value=mock_settings):
            result = _assert_workspace_path_consistency()
            assert result == tmp_path.resolve()


class TestRestoreCli:
    def test_restore_help(self) -> None:
        result = subprocess.run(
            [sys.executable, "scripts/restore.py", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=str(Path(__file__).resolve().parent.parent),
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
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert result.returncode != 0
