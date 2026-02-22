"""F1 verification: app.py lifespan injects compaction_settings into AgentLoop.

Tests that the real app.py assembly path produces an AgentLoop with
non-None _settings (compaction_settings). This covers the actual wiring,
not just unit-level mocks.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.gateway.app import lifespan


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
        patch("src.gateway.app.register_builtins"),
    ):
        settings = MagicMock()
        settings.workspace_dir = tmp_path
        settings.openai.api_key = "test-key"
        settings.openai.base_url = None
        settings.openai.model = "gpt-4o-mini"
        settings.database = MagicMock()
        settings.database.schema_ = "neomagi"
        settings.gateway.host = "0.0.0.0"
        settings.gateway.port = 19789
        settings.session.default_mode = "chat_safe"

        # Use real CompactionSettings so the wiring is verified end-to-end
        from src.config.settings import CompactionSettings

        settings.compaction = CompactionSettings()
        mock_settings.return_value = settings

        async with lifespan(app):
            agent_loop = app.state.agent_loop
            assert agent_loop._settings is not None
            assert agent_loop._budget_tracker is not None
            assert agent_loop._compaction_engine is not None
