"""Tests for src/infra/preflight.py — preflight checks and runner.

Each check is tested in isolation with mocks. run_preflight is tested
with composite scenarios (all-OK, mixed-WARN, FAIL-blocks).
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.health import CheckStatus
from src.infra.preflight import (
    _check_active_provider,
    _check_budget_tables,
    _check_db_connection,
    _check_schema_tables,
    _check_search_trigger,
    _check_soul_reconcile,
    _check_soul_versions_readable,
    _check_telegram_connector,
    _check_workspace_dirs,
    _check_workspace_path_consistency,
    run_preflight,
)

# ── Helpers ──


def _make_settings(**overrides: object) -> MagicMock:
    """Build a mock Settings object with sane defaults."""
    ws = overrides.pop("workspace_dir", Path("/tmp/test_ws"))
    settings = MagicMock()
    settings.workspace_dir = ws
    settings.memory.workspace_path = overrides.pop("memory_workspace_path", ws)
    settings.provider.active = overrides.pop("provider_active", "openai")
    settings.openai.api_key = overrides.pop("openai_api_key", "sk-test")
    settings.gemini.api_key = overrides.pop("gemini_api_key", "")
    settings.telegram.bot_token = overrides.pop("telegram_bot_token", "")
    settings.database.schema_ = "neomagi"
    return settings


def _async_ctx(obj: object) -> MagicMock:
    """Wrap obj in an async context manager mock."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=obj)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _mock_engine_ok() -> AsyncMock:
    """Create a mock engine whose .connect() succeeds with SELECT 1."""
    engine = AsyncMock()
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=MagicMock())
    engine.connect = MagicMock(return_value=_async_ctx(conn))
    return engine


def _mock_engine_with_db(
    tables: list[str] | None = None,
    budget_tables: list[str] | None = None,
    trigger_exists: bool = True,
) -> AsyncMock:
    """Create a mock engine with configurable DB responses."""
    if tables is None:
        tables = ["sessions", "messages", "memory_entries", "soul_versions"]
    if budget_tables is None:
        budget_tables = ["budget_state", "budget_reservations"]

    conn = AsyncMock()

    async def _execute(stmt, params=None):
        result = MagicMock()
        stmt_str = str(stmt) if not hasattr(stmt, "text") else str(stmt.text)
        if "information_schema.tables" in stmt_str:
            if "budget" in stmt_str:
                result.fetchall.return_value = [(t,) for t in budget_tables]
            else:
                result.fetchall.return_value = [(t,) for t in tables]
        elif "information_schema.triggers" in stmt_str:
            result.fetchone.return_value = (1,) if trigger_exists else None
        else:
            result.fetchone.return_value = (1,)
        return result

    conn.execute = _execute
    engine = AsyncMock()
    engine.connect = MagicMock(return_value=_async_ctx(conn))
    return engine


# ── C2: active_provider ──


class TestCheckActiveProvider:
    def test_openai_ok(self) -> None:
        s = _make_settings(provider_active="openai", openai_api_key="sk-test")
        r = _check_active_provider(s)
        assert r.status == CheckStatus.OK
        assert r.name == "active_provider"

    def test_openai_missing_key(self) -> None:
        s = _make_settings(provider_active="openai", openai_api_key="")
        r = _check_active_provider(s)
        assert r.status == CheckStatus.FAIL

    def test_gemini_ok(self) -> None:
        s = _make_settings(provider_active="gemini", gemini_api_key="key123")
        r = _check_active_provider(s)
        assert r.status == CheckStatus.OK

    def test_gemini_missing_key(self) -> None:
        s = _make_settings(provider_active="gemini", gemini_api_key="")
        r = _check_active_provider(s)
        assert r.status == CheckStatus.FAIL


# ── C3: workspace_path_consistency ──


class TestCheckWorkspacePathConsistency:
    def test_consistent(self) -> None:
        ws = Path("/tmp/test_ws")
        s = _make_settings(workspace_dir=ws, memory_workspace_path=ws)
        r = _check_workspace_path_consistency(s)
        assert r.status == CheckStatus.OK

    def test_inconsistent(self) -> None:
        s = _make_settings(
            workspace_dir=Path("/tmp/ws_a"),
            memory_workspace_path=Path("/tmp/ws_b"),
        )
        r = _check_workspace_path_consistency(s)
        assert r.status == CheckStatus.FAIL


# ── C4: workspace_dirs ──


class TestCheckWorkspaceDirs:
    def test_all_ok(self, tmp_path: Path) -> None:
        memory = tmp_path / "memory"
        memory.mkdir()
        s = _make_settings(workspace_dir=tmp_path)
        r = _check_workspace_dirs(s)
        assert r.status == CheckStatus.OK

    def test_ws_missing(self) -> None:
        s = _make_settings(workspace_dir=Path("/nonexistent_xyz_test"))
        r = _check_workspace_dirs(s)
        assert r.status == CheckStatus.FAIL
        assert "does not exist" in r.evidence

    def test_memory_dir_missing(self, tmp_path: Path) -> None:
        s = _make_settings(workspace_dir=tmp_path)
        r = _check_workspace_dirs(s)
        assert r.status == CheckStatus.FAIL
        assert "memory/" in r.evidence

    def test_memory_dir_not_writable(self, tmp_path: Path) -> None:
        memory = tmp_path / "memory"
        memory.mkdir()
        memory.chmod(0o444)
        try:
            s = _make_settings(workspace_dir=tmp_path)
            r = _check_workspace_dirs(s)
            assert r.status == CheckStatus.FAIL
            assert "not writable" in r.evidence
        finally:
            memory.chmod(0o755)


# ── C5: db_connection ──


class TestCheckDbConnection:
    @pytest.mark.asyncio
    async def test_ok(self) -> None:
        engine = _mock_engine_ok()
        r = await _check_db_connection(engine)
        assert r.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_fail(self) -> None:
        engine = AsyncMock()
        engine.connect = MagicMock(side_effect=ConnectionRefusedError("refused"))
        r = await _check_db_connection(engine)
        assert r.status == CheckStatus.FAIL
        assert "ConnectionRefusedError" in r.evidence


# ── C6: schema_tables ──


class TestCheckSchemaTables:
    @pytest.mark.asyncio
    async def test_all_present(self) -> None:
        engine = _mock_engine_with_db()
        r = await _check_schema_tables(engine)
        assert r.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_missing_tables(self) -> None:
        engine = _mock_engine_with_db(tables=["sessions"])
        r = await _check_schema_tables(engine)
        assert r.status == CheckStatus.FAIL
        assert "Missing tables" in r.evidence

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        engine = AsyncMock()
        engine.connect = MagicMock(side_effect=Exception("boom"))
        r = await _check_schema_tables(engine)
        assert r.status == CheckStatus.FAIL


# ── C7: search_trigger ──


class TestCheckSearchTrigger:
    @pytest.mark.asyncio
    async def test_exists(self) -> None:
        engine = _mock_engine_with_db(trigger_exists=True)
        r = await _check_search_trigger(engine)
        assert r.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_missing(self) -> None:
        engine = _mock_engine_with_db(trigger_exists=False)
        r = await _check_search_trigger(engine)
        assert r.status == CheckStatus.WARN

    @pytest.mark.asyncio
    async def test_exception_is_warn(self) -> None:
        engine = AsyncMock()
        engine.connect = MagicMock(side_effect=Exception("fail"))
        r = await _check_search_trigger(engine)
        assert r.status == CheckStatus.WARN


# ── C8: budget_tables ──


class TestCheckBudgetTables:
    @pytest.mark.asyncio
    async def test_present(self) -> None:
        engine = _mock_engine_with_db()
        r = await _check_budget_tables(engine)
        assert r.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_missing(self) -> None:
        engine = _mock_engine_with_db(budget_tables=["budget_state"])
        r = await _check_budget_tables(engine)
        assert r.status == CheckStatus.FAIL
        assert "budget_reservations" in r.evidence

    @pytest.mark.asyncio
    async def test_exception(self) -> None:
        engine = AsyncMock()
        engine.connect = MagicMock(side_effect=Exception("boom"))
        r = await _check_budget_tables(engine)
        assert r.status == CheckStatus.FAIL


# ── C9: soul_versions_readable ──


class TestCheckSoulVersionsReadable:
    @pytest.mark.asyncio
    async def test_readable(self) -> None:
        engine = _mock_engine_ok()
        r = await _check_soul_versions_readable(engine)
        assert r.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_fail(self) -> None:
        engine = AsyncMock()
        engine.connect = MagicMock(side_effect=Exception("no table"))
        r = await _check_soul_versions_readable(engine)
        assert r.status == CheckStatus.FAIL


# ── C10: telegram_connector ──


class TestCheckTelegramConnector:
    @pytest.mark.asyncio
    async def test_ok(self) -> None:
        s = _make_settings(telegram_bot_token="123:ABC")
        mock_bot = AsyncMock()
        mock_me = MagicMock()
        mock_me.username = "testbot"
        mock_bot.get_me = AsyncMock(return_value=mock_me)
        mock_bot.session = MagicMock()
        mock_bot.session.close = AsyncMock()

        # _check_telegram_connector does `from aiogram import Bot` inside the function
        # We must patch the Bot class at the aiogram module level
        with patch("aiogram.Bot", return_value=mock_bot):
            r = await _check_telegram_connector(s)
        assert r.status == CheckStatus.OK
        assert "testbot" in r.evidence

    @pytest.mark.asyncio
    async def test_fail(self) -> None:
        s = _make_settings(telegram_bot_token="bad_token")
        mock_bot = AsyncMock()
        mock_bot.get_me = AsyncMock(side_effect=Exception("Unauthorized"))
        mock_bot.session = MagicMock()
        mock_bot.session.close = AsyncMock()

        with patch("aiogram.Bot", return_value=mock_bot):
            r = await _check_telegram_connector(s)
        assert r.status == CheckStatus.FAIL


# ── C11: soul_reconcile ──


class TestCheckSoulReconcile:
    @pytest.mark.asyncio
    async def test_ok(self) -> None:
        s = _make_settings()
        engine = _mock_engine_ok()
        mock_evo = AsyncMock()
        mock_evo.reconcile_soul_projection = AsyncMock()

        # _check_soul_reconcile does `from src.memory.evolution import EvolutionEngine`
        # inside the function. Patch the class at its source module.
        with patch("src.memory.evolution.EvolutionEngine", return_value=mock_evo):
            r = await _check_soul_reconcile(s, engine)
        assert r.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_warn_on_error(self) -> None:
        s = _make_settings()
        engine = _mock_engine_ok()
        mock_evo = AsyncMock()
        mock_evo.reconcile_soul_projection = AsyncMock(side_effect=RuntimeError("oops"))

        with patch("src.memory.evolution.EvolutionEngine", return_value=mock_evo):
            r = await _check_soul_reconcile(s, engine)
        assert r.status == CheckStatus.WARN
        assert "oops" in r.evidence


# ── run_preflight composite tests ──


class TestRunPreflight:
    @pytest.mark.asyncio
    async def test_all_ok(self, tmp_path: Path) -> None:
        """All checks pass → report.passed is True."""
        memory = tmp_path / "memory"
        memory.mkdir()
        settings = _make_settings(workspace_dir=tmp_path, memory_workspace_path=tmp_path)
        settings.telegram.bot_token = ""

        engine = _mock_engine_with_db()
        mock_evo = AsyncMock()
        mock_evo.reconcile_soul_projection = AsyncMock()

        with patch("src.memory.evolution.EvolutionEngine", return_value=mock_evo):
            report = await run_preflight(settings, engine)

        assert report.passed is True
        assert all(c.status in (CheckStatus.OK, CheckStatus.WARN) for c in report.checks)

    @pytest.mark.asyncio
    async def test_fail_blocks(self, tmp_path: Path) -> None:
        """A FAIL check → report.passed is False."""
        memory = tmp_path / "memory"
        memory.mkdir()
        settings = _make_settings(
            workspace_dir=tmp_path,
            memory_workspace_path=tmp_path,
            openai_api_key="",
        )
        settings.telegram.bot_token = ""

        engine = _mock_engine_with_db()
        mock_evo = AsyncMock()
        mock_evo.reconcile_soul_projection = AsyncMock()

        with patch("src.memory.evolution.EvolutionEngine", return_value=mock_evo):
            report = await run_preflight(settings, engine)

        assert report.passed is False
        failed_names = [c.name for c in report.checks if c.status == CheckStatus.FAIL]
        assert "active_provider" in failed_names

    @pytest.mark.asyncio
    async def test_db_fail_skips_dependent(self) -> None:
        """DB connection fail → DB-dependent checks are skipped."""
        settings = _make_settings()
        settings.telegram.bot_token = ""

        engine = AsyncMock()
        engine.connect = MagicMock(side_effect=ConnectionRefusedError("refused"))

        report = await run_preflight(settings, engine)

        check_names = [c.name for c in report.checks]
        assert "db_connection" in check_names
        # DB-dependent checks should NOT be present when DB fails
        assert "schema_tables" not in check_names
        assert "search_trigger" not in check_names
        assert "budget_tables" not in check_names
        assert "soul_versions_readable" not in check_names
        # soul_reconcile should also be skipped (depends on DB)
        assert "soul_reconcile" not in check_names

    @pytest.mark.asyncio
    async def test_warn_does_not_block(self, tmp_path: Path) -> None:
        """WARN checks don't prevent passing."""
        memory = tmp_path / "memory"
        memory.mkdir()
        settings = _make_settings(workspace_dir=tmp_path, memory_workspace_path=tmp_path)
        settings.telegram.bot_token = ""

        engine = _mock_engine_with_db(trigger_exists=False)
        mock_evo = AsyncMock()
        mock_evo.reconcile_soul_projection = AsyncMock()

        with patch("src.memory.evolution.EvolutionEngine", return_value=mock_evo):
            report = await run_preflight(settings, engine)

        assert report.passed is True
        warn_names = [c.name for c in report.checks if c.status == CheckStatus.WARN]
        assert "search_trigger" in warn_names


# ── ValidationError wrapping test ──


class TestValidationErrorWrapping:
    def test_lifespan_wraps_validation_error(self) -> None:
        """Verify that the lifespan function has ValidationError try/except."""
        import ast
        import inspect

        from src.gateway.app import lifespan

        source = inspect.getsource(lifespan.__wrapped__)
        tree = ast.parse(source)

        found = _has_except_handler(tree, "ValidationError")
        assert found, (
            "lifespan should have try/except ValidationError wrapper for get_settings()"
        )


def _has_except_handler(tree, exception_name: str) -> bool:
    """Check if an AST tree contains an except handler for the given exception."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type and isinstance(node.type, ast.Name) and node.type.id == exception_name:
            return True
    return False
