"""Tests for Curator model parameterization (M6 Phase 1)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config.settings import MemorySettings
from src.memory.curator import MemoryCurator


def _make_settings(workspace: Path, **overrides) -> MemorySettings:
    defaults = {
        "workspace_path": workspace,
        "max_daily_note_bytes": 32_768,
        "daily_notes_load_days": 2,
        "daily_notes_max_tokens": 4000,
        "flush_min_confidence": 0.5,
        "curated_max_tokens": 4000,
        "curation_lookback_days": 7,
        "curation_temperature": 0.1,
    }
    defaults.update(overrides)
    return MemorySettings(**defaults)


def _make_mock_client(response_text: str = "updated content") -> MagicMock:
    from src.agent.model_client import ContentDelta

    async def fake_stream(*args, **kwargs):
        yield ContentDelta(text=response_text)

    client = MagicMock()
    client.chat_stream_with_tools = MagicMock(side_effect=fake_stream)
    return client


class TestCuratorModelParam:
    def test_uses_explicit_model(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        curator = MemoryCurator(MagicMock(), settings, model="custom-model")
        assert curator._model == "custom-model"

    def test_defaults_to_settings_curation_model(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path, curation_model="gpt-4o-mini")
        curator = MemoryCurator(MagicMock(), settings)
        assert curator._model == "gpt-4o-mini"

    def test_custom_curation_model_from_settings(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path, curation_model="gemini-2.5-flash")
        curator = MemoryCurator(MagicMock(), settings)
        assert curator._model == "gemini-2.5-flash"

    @pytest.mark.asyncio
    async def test_temperature_passed_to_model_client(self, tmp_path: Path) -> None:
        """Verify temperature kwarg is passed through to chat_stream_with_tools."""
        from datetime import date

        # Write a daily note so propose_updates gets called
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / f"{date.today().isoformat()}.md").write_text(
            "---\n[10:00] (source: user, scope: main)\ntest pattern\n"
            "---\n[11:00] (source: user, scope: main)\ntest pattern\n"
        )

        client = _make_mock_client("## Updated\nnew content")
        settings = _make_settings(tmp_path, curation_temperature=0.3)
        curator = MemoryCurator(client, settings, model="test-model")

        await curator.curate(tmp_path, scope_key="main")

        # Verify the model client was called with correct model and temperature
        client.chat_stream_with_tools.assert_called_once()
        call_args = client.chat_stream_with_tools.call_args
        # curator calls: chat_stream_with_tools(messages, model=self._model, temperature=...)
        _, kwargs = call_args
        assert kwargs["model"] == "test-model"
        assert kwargs["temperature"] == 0.3
