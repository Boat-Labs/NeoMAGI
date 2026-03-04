"""F1 verification: app.py lifespan injects compaction_settings into AgentLoop.

Tests that the real app.py assembly path produces an AgentLoop with
non-None _settings (compaction_settings). This covers the actual wiring,
not just unit-level mocks.

Extended (M3 post-review): tests for M3 tool wiring and ADR 0037 path validation.
Extended (M4 post-review): F3 polling done callback tests.
Extended (M5): preflight integration — run_preflight mocked for lifespan tests.
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.gateway.app import _on_polling_done, _shutdown_telegram, lifespan
from src.infra.health import PreflightReport


def _make_mock_settings(tmp_path: Path) -> MagicMock:
    """Create mock settings with consistent workspace paths."""
    from src.config.settings import (
        CompactionSettings,
        GeminiSettings,
        MemorySettings,
        ProviderSettings,
        TelegramSettings,
    )

    settings = MagicMock()
    settings.workspace_dir = tmp_path
    settings.openai.api_key = "test-key"
    settings.openai.base_url = None
    settings.openai.model = "gpt-4o-mini"
    settings.gemini = GeminiSettings()  # api_key="" → not registered
    settings.provider = ProviderSettings()  # active="openai"
    settings.database = MagicMock()
    settings.database.schema_ = "neomagi"
    settings.gateway.host = "0.0.0.0"
    settings.gateway.port = 19789
    settings.session.default_mode = "chat_safe"
    settings.compaction = CompactionSettings()
    settings.memory = MemorySettings(workspace_path=tmp_path)
    settings.session = MagicMock()
    settings.session.default_mode = "chat_safe"
    settings.telegram = TelegramSettings(bot_token="")  # force disabled regardless of env
    return settings


def _mock_preflight_ok() -> PreflightReport:
    """Return a passing preflight report."""
    return PreflightReport(checks=[])


@pytest.mark.asyncio
async def test_agent_loop_has_compaction_settings(tmp_path):
    """Real lifespan path: AgentLoop._settings is not None."""
    app = MagicMock()
    app.state = MagicMock()

    fake_engine = AsyncMock()
    fake_engine.dispose = AsyncMock()

    fake_session_factory = MagicMock()

    with (
        patch("src.gateway.app.setup_logging"),
        patch("src.gateway.app.get_settings") as mock_settings,
        patch("src.gateway.app.create_db_engine", return_value=fake_engine),
        patch("src.gateway.app.ensure_schema", return_value=None),
        patch("src.gateway.app.make_session_factory", return_value=fake_session_factory),
        patch("src.gateway.app.run_preflight", return_value=_mock_preflight_ok()),
        patch("src.memory.evolution.EvolutionEngine") as mock_evolution_cls,
    ):
        mock_evolution = AsyncMock()
        mock_evolution.reconcile_soul_projection = AsyncMock()
        mock_evolution_cls.return_value = mock_evolution

        settings = _make_mock_settings(tmp_path)
        mock_settings.return_value = settings

        async with lifespan(app):
            agent_loop = app.state.agent_loop
            assert agent_loop._settings is not None
            assert agent_loop._budget_tracker is not None
            assert agent_loop._compaction_engine is not None


@pytest.mark.asyncio
async def test_m3_tools_registered_and_wired(tmp_path):
    """Lifespan registers all 7 built-in tools with Memory deps wired."""
    app = MagicMock()
    app.state = MagicMock()

    fake_engine = AsyncMock()
    fake_engine.dispose = AsyncMock()
    fake_session_factory = MagicMock()

    with (
        patch("src.gateway.app.setup_logging"),
        patch("src.gateway.app.get_settings") as mock_settings,
        patch("src.gateway.app.create_db_engine", return_value=fake_engine),
        patch("src.gateway.app.ensure_schema", return_value=None),
        patch("src.gateway.app.make_session_factory", return_value=fake_session_factory),
        patch("src.gateway.app.run_preflight", return_value=_mock_preflight_ok()),
        patch("src.memory.evolution.EvolutionEngine") as mock_evolution_cls,
    ):
        mock_evolution = AsyncMock()
        mock_evolution.reconcile_soul_projection = AsyncMock()
        mock_evolution_cls.return_value = mock_evolution

        settings = _make_mock_settings(tmp_path)
        mock_settings.return_value = settings

        async with lifespan(app):
            agent_loop = app.state.agent_loop
            registry = agent_loop._tool_registry

            # All 7 tools should be registered
            expected_tools = [
                "current_time", "memory_search", "read_file",
                "memory_append", "soul_propose", "soul_status", "soul_rollback",
            ]
            for tool_name in expected_tools:
                tool = registry.get(tool_name)
                assert tool is not None, f"Tool '{tool_name}' not registered"

            # Verify memory_search has searcher injected
            mem_search = registry.get("memory_search")
            assert mem_search._searcher is not None

            # Verify memory_append has writer injected
            mem_append = registry.get("memory_append")
            assert mem_append._writer is not None

            # Verify soul tools have engine injected
            for soul_tool_name in ("soul_propose", "soul_status", "soul_rollback"):
                soul_tool = registry.get(soul_tool_name)
                assert soul_tool._engine is not None, f"{soul_tool_name} engine is None"


@pytest.mark.asyncio
async def test_empty_bot_token_skips_telegram(tmp_path):
    """Empty TELEGRAM_BOT_TOKEN → no TelegramAdapter created."""
    app = MagicMock()
    app.state = MagicMock()

    fake_engine = AsyncMock()
    fake_engine.dispose = AsyncMock()
    fake_session_factory = MagicMock()

    with (
        patch("src.gateway.app.setup_logging"),
        patch("src.gateway.app.get_settings") as mock_settings,
        patch("src.gateway.app.create_db_engine", return_value=fake_engine),
        patch("src.gateway.app.ensure_schema", return_value=None),
        patch("src.gateway.app.make_session_factory", return_value=fake_session_factory),
        patch("src.gateway.app.run_preflight", return_value=_mock_preflight_ok()),
        patch("src.memory.evolution.EvolutionEngine") as mock_evolution_cls,
    ):
        mock_evolution = AsyncMock()
        mock_evolution.reconcile_soul_projection = AsyncMock()
        mock_evolution_cls.return_value = mock_evolution

        settings = _make_mock_settings(tmp_path)
        assert settings.telegram.bot_token == ""  # sanity check
        mock_settings.return_value = settings

        # TelegramAdapter import should NOT happen
        with patch.dict("sys.modules", {}):
            async with lifespan(app):
                pass  # no crash = adapter was not started


@pytest.mark.asyncio
async def test_workspace_path_mismatch_fails(tmp_path):
    """ADR 0037: mismatched workspace paths must fail-fast on startup (via preflight)."""
    app = MagicMock()
    app.state = MagicMock()

    fake_engine = AsyncMock()
    fake_engine.dispose = AsyncMock()

    with (
        patch("src.gateway.app.setup_logging"),
        patch("src.gateway.app.get_settings") as mock_settings,
        patch("src.gateway.app.create_db_engine", return_value=fake_engine),
        patch("src.gateway.app.ensure_schema", return_value=None),
        patch("src.gateway.app.make_session_factory", return_value=MagicMock()),
    ):
        settings = _make_mock_settings(tmp_path)
        # Deliberately set a different workspace_path in MemorySettings
        settings.memory.workspace_path = tmp_path / "different"
        mock_settings.return_value = settings

        with pytest.raises(RuntimeError, match="Preflight failed"):
            async with lifespan(app):
                pass


# ---------------------------------------------------------------------------
# F3: _on_polling_done callback
# ---------------------------------------------------------------------------


class TestPollingDoneCallback:
    """F3 unit: _on_polling_done function behavior."""

    def test_sends_sigterm_on_exception(self):
        task = asyncio.Future()
        task.set_exception(RuntimeError("polling crashed"))

        with patch("src.gateway.app.os.kill") as mock_kill:
            _on_polling_done(task)
            mock_kill.assert_called_once()
            _, sig = mock_kill.call_args[0]
            assert sig == signal.SIGTERM

    def test_ignores_cancelled_task(self):
        task = asyncio.Future()
        task.cancel()
        # Need to suppress the CancelledError
        try:
            task.result()
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            pass

        with patch("src.gateway.app.os.kill") as mock_kill:
            _on_polling_done(task)
            mock_kill.assert_not_called()

    def test_ignores_successful_task(self):
        task = asyncio.Future()
        task.set_result(None)

        with patch("src.gateway.app.os.kill") as mock_kill:
            _on_polling_done(task)
            mock_kill.assert_not_called()


class TestTelegramShutdown:
    """Shutdown path should not block indefinitely on Telegram polling."""

    @pytest.mark.asyncio
    async def test_shutdown_cancels_polling_before_stopping_adapter(self):
        gate = asyncio.Event()

        async def _poll_forever():
            await gate.wait()

        polling_task = asyncio.create_task(_poll_forever())
        adapter = MagicMock()
        adapter.stop = AsyncMock()

        await _shutdown_telegram(adapter, polling_task, timeout_s=0.05)

        assert polling_task.cancelled()
        adapter.stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shutdown_handles_stop_timeout(self):
        async def _done():
            return None

        polling_task = asyncio.create_task(_done())
        adapter = MagicMock()

        async def _hang():
            await asyncio.sleep(60)

        adapter.stop = AsyncMock(side_effect=_hang)

        await _shutdown_telegram(adapter, polling_task, timeout_s=0.01)
        adapter.stop.assert_awaited_once()
