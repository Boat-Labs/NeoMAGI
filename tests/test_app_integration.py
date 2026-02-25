"""F1 verification: app.py lifespan injects compaction_settings into AgentLoop.

Tests that the real app.py assembly path produces an AgentLoop with
non-None _settings (compaction_settings). This covers the actual wiring,
not just unit-level mocks.

Extended (M3 post-review): tests for M3 tool wiring and ADR 0037 path validation.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.gateway.app import lifespan


def _make_mock_settings(tmp_path: Path) -> MagicMock:
    """Create mock settings with consistent workspace paths."""
    from src.config.settings import CompactionSettings, GeminiSettings, MemorySettings, ProviderSettings

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
    return settings


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
        patch("src.gateway.app.EvolutionEngine") as mock_evolution_cls,
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
        patch("src.gateway.app.EvolutionEngine") as mock_evolution_cls,
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
async def test_workspace_path_mismatch_fails(tmp_path):
    """ADR 0037: mismatched workspace paths must fail-fast on startup."""
    app = MagicMock()
    app.state = MagicMock()

    with (
        patch("src.gateway.app.setup_logging"),
        patch("src.gateway.app.get_settings") as mock_settings,
    ):
        settings = _make_mock_settings(tmp_path)
        # Deliberately set a different workspace_path in MemorySettings
        settings.memory.workspace_path = tmp_path / "different"
        mock_settings.return_value = settings

        with pytest.raises(RuntimeError, match="workspace_path mismatch"):
            async with lifespan(app):
                pass
