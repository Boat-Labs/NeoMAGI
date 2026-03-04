"""Tests for scripts/backup.py — backup script unit tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestBackupPgDumpCheck:
    def test_pg_dump_not_found_exits(self) -> None:
        from scripts.backup import _check_pg_dump

        with patch("shutil.which", return_value=None):
            with pytest.raises(SystemExit) as exc_info:
                _check_pg_dump()
            assert exc_info.value.code == 1

    def test_pg_dump_found_returns_path(self) -> None:
        from scripts.backup import _check_pg_dump

        with patch("shutil.which", return_value="/usr/bin/pg_dump"):
            result = _check_pg_dump()
            assert result == "/usr/bin/pg_dump"


class TestBackupTruthTables:
    def test_truth_tables_list(self) -> None:
        from scripts.backup import TRUTH_TABLES

        assert len(TRUTH_TABLES) == 5
        assert "neomagi.sessions" in TRUTH_TABLES
        assert "neomagi.messages" in TRUTH_TABLES
        assert "neomagi.soul_versions" in TRUTH_TABLES
        assert "neomagi.budget_state" in TRUTH_TABLES
        assert "neomagi.budget_reservations" in TRUTH_TABLES

    def test_memory_entries_excluded(self) -> None:
        from scripts.backup import TRUTH_TABLES

        assert "neomagi.memory_entries" not in TRUTH_TABLES
        for t in TRUTH_TABLES:
            assert "memory_entries" not in t


class TestRunBackup:
    @patch("scripts.backup.subprocess.run")
    @patch("scripts.backup._check_pg_dump", return_value="/usr/bin/pg_dump")
    @patch("scripts.backup._get_dsn", return_value="postgresql://user@localhost/neomagi")
    def test_pg_dump_called_with_table_args(
        self,
        mock_dsn: MagicMock,
        mock_check: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        from scripts.backup import TRUTH_TABLES, run_backup

        # Mock successful pg_dump and tar
        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")

        # Create workspace dirs for tar
        ws = tmp_path / "workspace" / "memory"
        ws.mkdir(parents=True)
        (ws / "test.md").write_text("test")
        (tmp_path / "workspace" / "MEMORY.md").write_text("test")

        output_dir = tmp_path / "backups"

        ws_patch = patch(
            "scripts.backup.Path",
            side_effect=lambda x: tmp_path / x if x == "workspace" else Path(x),
        )
        with ws_patch:
            # Simplified: just test the pg_dump call args
            run_backup(output_dir)

        # First subprocess.run call should be pg_dump
        pg_dump_call = mock_run.call_args_list[0]
        cmd = pg_dump_call[0][0]

        # Verify --table args for all 5 truth tables
        table_args = []
        for i, arg in enumerate(cmd):
            if arg == "--table" and i + 1 < len(cmd):
                table_args.append(cmd[i + 1])

        assert len(table_args) == 5
        for t in TRUTH_TABLES:
            assert t in table_args

        # Verify memory_entries NOT in table args
        assert "neomagi.memory_entries" not in table_args

    @patch("scripts.backup.subprocess.run")
    @patch("scripts.backup._check_pg_dump", return_value="/usr/bin/pg_dump")
    @patch("scripts.backup._get_dsn", return_value="postgresql://user@localhost/neomagi")
    def test_manifest_created(
        self,
        mock_dsn: MagicMock,
        mock_check: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        from scripts.backup import run_backup

        mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
        output_dir = tmp_path / "backups"

        ws_patch = patch(
            "scripts.backup.Path",
            side_effect=lambda x: tmp_path / x if x == "workspace" else Path(x),
        )
        with ws_patch:
            run_backup(output_dir)

        manifest_files = list(output_dir.glob("manifest_*.txt"))
        assert len(manifest_files) == 1
        content = manifest_files[0].read_text()
        assert "NeoMAGI Backup Manifest" in content

    @patch("scripts.backup.subprocess.run")
    @patch("scripts.backup._check_pg_dump", return_value="/usr/bin/pg_dump")
    @patch("scripts.backup._get_dsn", return_value="postgresql://user@localhost/neomagi")
    def test_pg_dump_failure_exits(
        self,
        mock_dsn: MagicMock,
        mock_check: MagicMock,
        mock_run: MagicMock,
        tmp_path: Path,
    ) -> None:
        from scripts.backup import run_backup

        mock_run.return_value = MagicMock(returncode=1, stderr="connection refused", stdout="")
        output_dir = tmp_path / "backups"

        with pytest.raises(SystemExit) as exc_info:
            run_backup(output_dir)
        assert exc_info.value.code == 1


class TestBackupCli:
    def test_backup_help(self) -> None:
        import sys

        result = subprocess.run(
            [sys.executable, "scripts/backup.py", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd="/Users/zhiliangzhou/devel/Zhiliang/NeoMAGI/.claude/worktrees/backend-m5",
        )
        assert result.returncode == 0
        assert "--output-dir" in result.stdout
