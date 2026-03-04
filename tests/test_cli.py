"""Tests for src/backend/cli.py — CLI entry point smoke tests."""

from __future__ import annotations

import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.backend.cli import _build_parser


class TestCliParser:
    def test_help_output(self) -> None:
        """--help should produce output without error."""
        parser = _build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--help"])
        assert exc_info.value.code == 0

    def test_doctor_subcommand(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["doctor"])
        assert args.command == "doctor"
        assert args.deep is False

    def test_doctor_deep_flag(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["doctor", "--deep"])
        assert args.command == "doctor"
        assert args.deep is True

    def test_no_command_returns_none(self) -> None:
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.command is None

    def test_reindex_subcommand(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["reindex"])
        assert args.command == "reindex"
        assert args.scope == "main"

    def test_reindex_scope_flag(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["reindex", "--scope", "test"])
        assert args.command == "reindex"
        assert args.scope == "test"

    def test_reconcile_subcommand(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["reconcile"])
        assert args.command == "reconcile"


class TestCliModule:
    def test_module_help(self) -> None:
        """python -m src.backend.cli --help should exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "src.backend.cli", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "doctor" in result.stdout

    def test_doctor_help(self) -> None:
        """python -m src.backend.cli doctor --help should exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "src.backend.cli", "doctor", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "--deep" in result.stdout

    def test_reindex_help(self) -> None:
        """python -m src.backend.cli reindex --help should exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "src.backend.cli", "reindex", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "--scope" in result.stdout

    def test_reconcile_help(self) -> None:
        """python -m src.backend.cli reconcile --help should exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "src.backend.cli", "reconcile", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0


def _make_async_cm(return_value):
    """Create a proper async context manager mock."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=return_value)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


class TestReindexCli:
    @pytest.mark.asyncio
    async def test_truncate_before_reindex(self) -> None:
        """TRUNCATE must execute before reindex_all."""
        from src.backend.cli import _run_reindex

        execution_log: list[str] = []

        mock_settings = MagicMock()
        mock_settings.database = MagicMock()
        mock_settings.memory = MagicMock()

        mock_conn = AsyncMock()

        async def track_execute(stmt):
            stmt_str = str(stmt) if not hasattr(stmt, "text") else str(stmt.text)
            if "COUNT" in stmt_str:
                return MagicMock(scalar=lambda: 15)
            if "TRUNCATE" in stmt_str:
                execution_log.append("truncate")
            return MagicMock()

        mock_conn.execute = track_execute

        mock_engine = MagicMock()
        mock_engine.begin = MagicMock(return_value=_make_async_cm(mock_conn))
        mock_engine.dispose = AsyncMock()

        mock_indexer = AsyncMock()

        async def track_reindex(**kw):
            execution_log.append("reindex_all")
            return 20

        mock_indexer.reindex_all = track_reindex

        with (
            patch("src.config.settings.get_settings", return_value=mock_settings),
            patch("src.session.database.create_db_engine", return_value=mock_engine),
            patch("src.session.database.make_session_factory", return_value=MagicMock()),
            patch("src.memory.indexer.MemoryIndexer", return_value=mock_indexer),
        ):
            code = await _run_reindex("main")

        assert code == 0
        assert execution_log == ["truncate", "reindex_all"]


class TestReconcileCli:
    @pytest.mark.asyncio
    async def test_reconcile_calls_evolution(self) -> None:
        """reconcile should call EvolutionEngine.reconcile_soul_projection."""
        from src.backend.cli import _run_reconcile

        mock_settings = MagicMock()
        mock_settings.database = MagicMock()
        mock_settings.memory.workspace_path = MagicMock()

        mock_engine = AsyncMock()
        mock_engine.dispose = AsyncMock()

        mock_evolution = AsyncMock()
        mock_evolution.reconcile_soul_projection = AsyncMock()

        with (
            patch("src.config.settings.get_settings", return_value=mock_settings),
            patch("src.session.database.create_db_engine", return_value=mock_engine),
            patch("src.session.database.make_session_factory", return_value=MagicMock()),
            patch("src.memory.evolution.EvolutionEngine", return_value=mock_evolution),
        ):
            code = await _run_reconcile()

        assert code == 0
        mock_evolution.reconcile_soul_projection.assert_called_once()

    @pytest.mark.asyncio
    async def test_reconcile_failure_returns_1(self) -> None:
        """reconcile failure should return exit code 1."""
        from src.backend.cli import _run_reconcile

        mock_settings = MagicMock()
        mock_settings.database = MagicMock()
        mock_settings.memory.workspace_path = MagicMock()

        mock_engine = AsyncMock()
        mock_engine.dispose = AsyncMock()

        mock_evolution = AsyncMock()
        mock_evolution.reconcile_soul_projection = AsyncMock(
            side_effect=RuntimeError("test error")
        )

        with (
            patch("src.config.settings.get_settings", return_value=mock_settings),
            patch("src.session.database.create_db_engine", return_value=mock_engine),
            patch("src.session.database.make_session_factory", return_value=MagicMock()),
            patch("src.memory.evolution.EvolutionEngine", return_value=mock_evolution),
        ):
            code = await _run_reconcile()

        assert code == 1
