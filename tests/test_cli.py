"""Tests for src/backend/cli.py — CLI entry point smoke tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
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

    def test_init_soul_subcommand(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["init-soul"])
        assert args.command == "init-soul"
        assert args.source is None

    def test_init_soul_source_flag(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["init-soul", "--from", "custom.md"])
        assert args.command == "init-soul"
        assert args.source == "custom.md"

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

    def test_reset_user_db_subcommand(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["reset-user-db"])
        assert args.command == "reset-user-db"
        assert args.yes is False

    def test_reset_user_db_yes_flag(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["reset-user-db", "--yes"])
        assert args.command == "reset-user-db"
        assert args.yes is True

    def test_reconcile_subcommand(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["reconcile"])
        assert args.command == "reconcile"

    def test_check_governance_tables_subcommand(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["check-governance-tables"])
        assert args.command == "check-governance-tables"


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
        assert "init-soul" in result.stdout
        assert "reset-user-db" in result.stdout

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

    def test_init_soul_help(self) -> None:
        """python -m src.backend.cli init-soul --help should exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "src.backend.cli", "init-soul", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "--from" in result.stdout

    def test_reset_user_db_help(self) -> None:
        """python -m src.backend.cli reset-user-db --help should exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "src.backend.cli", "reset-user-db", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "--yes" in result.stdout

    def test_reconcile_help(self) -> None:
        """python -m src.backend.cli reconcile --help should exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "src.backend.cli", "reconcile", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0

    def test_check_governance_tables_help(self) -> None:
        """python -m src.backend.cli check-governance-tables --help should exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "src.backend.cli", "check-governance-tables", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "governance" in result.stdout.lower()


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


class TestResetUserDbCli:
    @pytest.mark.asyncio
    async def test_requires_explicit_confirmation(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """reset-user-db must refuse to run without --yes."""
        from src.backend.cli import _run_reset_user_db

        mock_db = MagicMock(host="localhost", port=5432, name="neomagi", schema_="neomagi")

        with patch("src.config.settings.DatabaseSettings", return_value=mock_db):
            code = await _run_reset_user_db(confirm=False)

        assert code == 1
        out = capsys.readouterr().out
        assert "Rerun with --yes" in out

    @pytest.mark.asyncio
    async def test_drop_then_upgrade_then_ensure_schema(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """reset-user-db should wipe schema, run Alembic, then ensure idempotent extras."""
        from src.backend.cli import _run_reset_user_db

        execution_log: list[str] = []
        mock_db = MagicMock(host="localhost", port=5432, name="neomagi", schema_="neomagi")

        mock_conn = AsyncMock()

        async def track_execute(stmt):
            stmt_str = str(stmt) if not hasattr(stmt, "text") else str(stmt.text)
            if "DROP SCHEMA IF EXISTS neomagi CASCADE" in stmt_str:
                execution_log.append("drop_schema")
            return MagicMock()

        mock_conn.execute = track_execute

        drop_engine = MagicMock()
        drop_engine.begin = MagicMock(return_value=_make_async_cm(mock_conn))
        drop_engine.dispose = AsyncMock()

        ensure_engine = MagicMock()
        ensure_engine.dispose = AsyncMock()

        async def track_ensure_schema(engine, schema):
            assert engine is ensure_engine
            assert schema == "neomagi"
            execution_log.append("ensure_schema")

        def track_upgrade() -> None:
            execution_log.append("alembic_upgrade")

        with (
            patch("src.config.settings.DatabaseSettings", return_value=mock_db),
            patch(
                "src.session.database.create_db_engine",
                side_effect=[drop_engine, ensure_engine],
            ),
            patch("src.session.database.ensure_schema", side_effect=track_ensure_schema),
            patch("src.backend.cli._run_alembic_upgrade_head", side_effect=track_upgrade),
        ):
            code = await _run_reset_user_db(confirm=True)

        assert code == 0
        assert execution_log == ["drop_schema", "alembic_upgrade", "ensure_schema"]
        out = capsys.readouterr().out
        assert "reset-user-db complete" in out


class TestInitSoulCli:
    @pytest.mark.asyncio
    async def test_init_soul_writes_default_and_bootstraps(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """init-soul should write workspace/SOUL.md from the default template and bootstrap it."""
        from src.backend.cli import _run_init_soul

        workspace_dir = tmp_path / "workspace"
        template = tmp_path / "SOUL.default.md"
        template.write_text("# SOUL.md - Who You Are\nseed\n", encoding="utf-8")

        mock_settings = MagicMock()
        mock_settings.database = MagicMock()
        mock_settings.memory = MagicMock()
        mock_settings.workspace_dir = workspace_dir

        mock_engine = AsyncMock()
        mock_engine.dispose = AsyncMock()

        active_version = None

        async def get_current_version():
            return active_version

        async def ensure_bootstrap(workspace_path):
            nonlocal active_version
            assert workspace_path == workspace_dir
            active_version = MagicMock(version=0)

        mock_evolution = MagicMock()
        mock_evolution.get_current_version = AsyncMock(side_effect=get_current_version)
        mock_evolution.ensure_bootstrap = AsyncMock(side_effect=ensure_bootstrap)

        with (
            patch("src.backend.cli._default_soul_template_path", return_value=template),
            patch("src.config.settings.get_settings", return_value=mock_settings),
            patch("src.session.database.create_db_engine", return_value=mock_engine),
            patch("src.session.database.make_session_factory", return_value=MagicMock()),
            patch("src.memory.evolution.EvolutionEngine", return_value=mock_evolution),
        ):
            code = await _run_init_soul(source=None)

        assert code == 0
        assert (workspace_dir / "SOUL.md").read_text(encoding="utf-8") == template.read_text(
            encoding="utf-8"
        )
        out = capsys.readouterr().out
        assert "explicit SOUL bootstrap" in out
        assert "database becomes the truth source" in out
        assert "active SOUL version v0" in out

    @pytest.mark.asyncio
    async def test_init_soul_uses_custom_source_when_requested(
        self, tmp_path: Path
    ) -> None:
        """init-soul should copy the requested source file into workspace/SOUL.md."""
        from src.backend.cli import _run_init_soul

        workspace_dir = tmp_path / "workspace"
        source = tmp_path / "custom-soul.md"
        source.write_text("# Custom Soul\ncustom\n", encoding="utf-8")

        mock_settings = MagicMock()
        mock_settings.database = MagicMock()
        mock_settings.memory = MagicMock()
        mock_settings.workspace_dir = workspace_dir

        mock_engine = AsyncMock()
        mock_engine.dispose = AsyncMock()

        active_version = None

        async def get_current_version():
            return active_version

        async def ensure_bootstrap(workspace_path):
            nonlocal active_version
            assert workspace_path == workspace_dir
            active_version = MagicMock(version=0)

        mock_evolution = MagicMock()
        mock_evolution.get_current_version = AsyncMock(side_effect=get_current_version)
        mock_evolution.ensure_bootstrap = AsyncMock(side_effect=ensure_bootstrap)

        with (
            patch("src.config.settings.get_settings", return_value=mock_settings),
            patch("src.session.database.create_db_engine", return_value=mock_engine),
            patch("src.session.database.make_session_factory", return_value=MagicMock()),
            patch("src.memory.evolution.EvolutionEngine", return_value=mock_evolution),
        ):
            code = await _run_init_soul(source=str(source))

        assert code == 0
        assert (workspace_dir / "SOUL.md").read_text(encoding="utf-8") == "# Custom Soul\ncustom\n"

    @pytest.mark.asyncio
    async def test_init_soul_refuses_when_db_already_seeded(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """init-soul should refuse to overwrite an already-initialized DB truth source."""
        from src.backend.cli import _run_init_soul

        workspace_dir = tmp_path / "workspace"
        template = tmp_path / "SOUL.default.md"
        template.write_text("# SOUL.md - Who You Are\nseed\n", encoding="utf-8")

        mock_settings = MagicMock()
        mock_settings.database = MagicMock()
        mock_settings.memory = MagicMock()
        mock_settings.workspace_dir = workspace_dir

        mock_engine = AsyncMock()
        mock_engine.dispose = AsyncMock()

        active = MagicMock(version=3)
        mock_evolution = MagicMock()
        mock_evolution.get_current_version = AsyncMock(return_value=active)
        mock_evolution.ensure_bootstrap = AsyncMock()

        with (
            patch("src.backend.cli._default_soul_template_path", return_value=template),
            patch("src.config.settings.get_settings", return_value=mock_settings),
            patch("src.session.database.create_db_engine", return_value=mock_engine),
            patch("src.session.database.make_session_factory", return_value=MagicMock()),
            patch("src.memory.evolution.EvolutionEngine", return_value=mock_evolution),
        ):
            code = await _run_init_soul(source=None)

        assert code == 1
        out = capsys.readouterr().out
        assert "already has active SOUL version v3" in out
        mock_evolution.ensure_bootstrap.assert_not_called()

    @pytest.mark.asyncio
    async def test_init_soul_refuses_conflicting_existing_file(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """init-soul should fail closed if workspace/SOUL.md conflicts with requested source."""
        from src.backend.cli import _run_init_soul

        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "SOUL.md").write_text("# Existing Soul\n", encoding="utf-8")

        source = tmp_path / "custom-soul.md"
        source.write_text("# Requested Soul\n", encoding="utf-8")

        mock_settings = MagicMock()
        mock_settings.database = MagicMock()
        mock_settings.memory = MagicMock()
        mock_settings.workspace_dir = workspace_dir

        mock_engine = AsyncMock()
        mock_engine.dispose = AsyncMock()

        mock_evolution = MagicMock()
        mock_evolution.get_current_version = AsyncMock(return_value=None)
        mock_evolution.ensure_bootstrap = AsyncMock()

        with (
            patch("src.config.settings.get_settings", return_value=mock_settings),
            patch("src.session.database.create_db_engine", return_value=mock_engine),
            patch("src.session.database.make_session_factory", return_value=MagicMock()),
            patch("src.memory.evolution.EvolutionEngine", return_value=mock_evolution),
        ):
            code = await _run_init_soul(source=str(source))

        assert code == 1
        err = capsys.readouterr().err
        assert "differs from the requested source" in err
        mock_evolution.ensure_bootstrap.assert_not_called()


class TestCheckGovernanceTablesCli:
    @pytest.mark.asyncio
    async def test_prints_all_table_counts(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """check-governance-tables should print counts for all 6 governance tables."""
        from src.backend.cli import _run_check_governance_tables

        mock_settings = MagicMock()
        mock_db = MagicMock(host="localhost", schema_="neomagi")
        mock_db.name = "neomagi"
        mock_settings.database = mock_db

        call_count = 0

        mock_conn = AsyncMock()

        async def track_execute(stmt):
            nonlocal call_count
            call_count += 1
            return MagicMock(scalar_one=lambda: call_count * 10)

        mock_conn.execute = track_execute

        mock_engine = MagicMock()
        mock_engine.connect = MagicMock(return_value=_make_async_cm(mock_conn))
        mock_engine.dispose = AsyncMock()

        with (
            patch("src.config.settings.get_settings", return_value=mock_settings),
            patch("src.session.database.create_db_engine", return_value=mock_engine),
        ):
            code = await _run_check_governance_tables()

        assert code == 0
        out = capsys.readouterr().out
        assert "db=neomagi" in out
        assert "soul_versions:" in out
        assert "skill_specs:" in out
        assert "skill_spec_versions:" in out
        assert "wrapper_tools:" in out
        assert "wrapper_tool_versions:" in out
        assert call_count == 6


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
