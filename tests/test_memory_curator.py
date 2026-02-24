"""Tests for MemoryCurator.

Covers:
- curate: normal flow / no daily notes / MEMORY.md not found / size truncation
- propose_updates: LLM mock / no changes
- _read_recent_daily_notes: normal / no files / missing dir
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import MemorySettings
from src.memory.curator import MemoryCurator


def _make_settings(workspace: Path) -> MemorySettings:
    return MemorySettings(
        workspace_path=workspace,
        max_daily_note_bytes=32_768,
        daily_notes_load_days=2,
        daily_notes_max_tokens=4000,
        flush_min_confidence=0.5,
        curated_max_tokens=4000,
        curation_lookback_days=7,
        curation_temperature=0.1,
    )


def _write_daily_note(workspace: Path, target_date: date, content: str) -> Path:
    memory_dir = workspace / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    filepath = memory_dir / f"{target_date.isoformat()}.md"
    filepath.write_text(content, encoding="utf-8")
    return filepath


def _make_mock_model_client(response_text: str) -> MagicMock:
    """Create a mock ModelClient that streams response_text."""
    from src.agent.model_client import ContentDelta

    async def fake_stream(*args, **kwargs):
        yield ContentDelta(text=response_text)

    client = MagicMock()
    client.chat_stream_with_tools = MagicMock(side_effect=fake_stream)
    return client


class TestCurateNormal:
    @pytest.mark.asyncio
    async def test_curate_creates_memory_md(self, tmp_path: Path) -> None:
        today = date.today()
        _write_daily_note(
            tmp_path, today,
            "---\n[10:00] (source: user, scope: main)\nUser prefers dark mode\n"
            "---\n[11:00] (source: user, scope: main)\nUser prefers dark mode\n"
        )

        new_content = "## Preferences\nUser prefers dark mode"
        model_client = _make_mock_model_client(new_content)
        settings = _make_settings(tmp_path)
        curator = MemoryCurator(model_client, settings)

        result = await curator.curate(tmp_path, scope_key="main")

        assert result.status == "updated"
        assert result.new_content == new_content

        # Verify file was written
        memory_md = tmp_path / "MEMORY.md"
        assert memory_md.is_file()
        assert "dark mode" in memory_md.read_text()


class TestCurateNoNotes:
    @pytest.mark.asyncio
    async def test_no_daily_notes_skips(self, tmp_path: Path) -> None:
        model_client = MagicMock()
        settings = _make_settings(tmp_path)
        curator = MemoryCurator(model_client, settings)

        result = await curator.curate(tmp_path)

        assert result.status == "no_changes"
        # Model client should NOT be called
        model_client.chat_stream_with_tools.assert_not_called()


class TestCurateNoChanges:
    @pytest.mark.asyncio
    async def test_same_content_returns_no_changes(self, tmp_path: Path) -> None:
        today = date.today()
        _write_daily_note(tmp_path, today, "---\n[10:00] (source: user)\nSome note")

        existing_content = "## Existing\nSome curated content"
        (tmp_path / "MEMORY.md").write_text(existing_content)

        # LLM returns same content
        model_client = _make_mock_model_client(existing_content)
        settings = _make_settings(tmp_path)
        curator = MemoryCurator(model_client, settings)

        result = await curator.curate(tmp_path)

        assert result.status == "no_changes"


class TestCurateSizeTruncation:
    @pytest.mark.asyncio
    async def test_truncates_large_output(self, tmp_path: Path) -> None:
        today = date.today()
        _write_daily_note(tmp_path, today, "---\n[10:00] (source: user)\nNote")

        # LLM returns huge content
        huge_content = "## Big\n" + "x" * 50_000
        model_client = _make_mock_model_client(huge_content)
        settings = _make_settings(tmp_path)
        settings = MemorySettings(
            workspace_path=tmp_path,
            max_daily_note_bytes=32_768,
            daily_notes_load_days=2,
            daily_notes_max_tokens=4000,
            flush_min_confidence=0.5,
            curated_max_tokens=100,  # Very small limit
            curation_lookback_days=7,
            curation_temperature=0.1,
        )
        curator = MemoryCurator(model_client, settings)

        result = await curator.curate(tmp_path)

        assert result.status == "updated"
        # Content should be truncated (100 tokens * 4 chars = 400)
        assert len(result.new_content) <= 400


class TestCurateWithIndexer:
    @pytest.mark.asyncio
    async def test_reindexes_after_curation(self, tmp_path: Path) -> None:
        today = date.today()
        _write_daily_note(tmp_path, today, "---\n[10:00] (source: user)\nNote")

        new_content = "## Updated\nNew curated content"
        model_client = _make_mock_model_client(new_content)
        indexer = MagicMock()
        indexer.index_curated_memory = AsyncMock(return_value=1)

        settings = _make_settings(tmp_path)
        curator = MemoryCurator(model_client, settings, indexer=indexer)

        await curator.curate(tmp_path, scope_key="main")

        indexer.index_curated_memory.assert_called_once()


class TestProposeUpdates:
    @pytest.mark.asyncio
    async def test_returns_proposal(self, tmp_path: Path) -> None:
        expected = "## Updated Section\nNew content here"
        model_client = _make_mock_model_client(expected)
        settings = _make_settings(tmp_path)
        curator = MemoryCurator(model_client, settings)

        proposal = await curator.propose_updates(
            daily_content="=== 2026-02-24 ===\nSome note",
            current_curated="## Old\nOld content",
        )

        assert proposal.new_content == expected


class TestCurateEmptyLLMOutput:
    @pytest.mark.asyncio
    async def test_curate_empty_llm_output_preserves_memory(self, tmp_path: Path) -> None:
        """Empty LLM output must NOT overwrite existing MEMORY.md."""
        today = date.today()
        _write_daily_note(tmp_path, today, "---\n[10:00] (source: user)\nSome note")

        existing_content = "## Existing\nImportant curated content"
        (tmp_path / "MEMORY.md").write_text(existing_content)

        # LLM returns empty string
        model_client = _make_mock_model_client("")
        settings = _make_settings(tmp_path)
        curator = MemoryCurator(model_client, settings)

        result = await curator.curate(tmp_path)

        assert result.status == "no_changes"
        # MEMORY.md should be preserved
        assert (tmp_path / "MEMORY.md").read_text() == existing_content

    @pytest.mark.asyncio
    async def test_curate_whitespace_llm_output_preserves_memory(self, tmp_path: Path) -> None:
        """Whitespace-only LLM output must NOT overwrite existing MEMORY.md."""
        today = date.today()
        _write_daily_note(tmp_path, today, "---\n[10:00] (source: user)\nSome note")

        existing_content = "## Existing\nImportant content"
        (tmp_path / "MEMORY.md").write_text(existing_content)

        # LLM returns whitespace only
        model_client = _make_mock_model_client("   \n\n  ")
        settings = _make_settings(tmp_path)
        curator = MemoryCurator(model_client, settings)

        result = await curator.curate(tmp_path)

        assert result.status == "no_changes"
        assert (tmp_path / "MEMORY.md").read_text() == existing_content


class TestReadRecentDailyNotes:
    def test_reads_recent_files(self, tmp_path: Path) -> None:
        today = date.today()
        _write_daily_note(tmp_path, today, "Today note")
        _write_daily_note(tmp_path, today - timedelta(days=1), "Yesterday note")

        result = MemoryCurator._read_recent_daily_notes(tmp_path, days=3)

        assert "Today note" in result
        assert "Yesterday note" in result
        assert f"=== {today.isoformat()} ===" in result

    def test_no_memory_dir(self, tmp_path: Path) -> None:
        result = MemoryCurator._read_recent_daily_notes(tmp_path)
        assert result == ""

    def test_empty_dir(self, tmp_path: Path) -> None:
        (tmp_path / "memory").mkdir()
        result = MemoryCurator._read_recent_daily_notes(tmp_path)
        assert result == ""
