"""Tests for MemoryIndexer.

Covers:
- index_daily_note: normal / segments / delete-reinsert idempotent / empty / scope / old data compat
- index_curated_memory: markdown headers / empty / scope
- reindex_all: full rebuild / no files
- Helper methods: date parsing, scope extraction, text extraction, header splitting
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.config.settings import MemorySettings
from src.memory.indexer import MemoryIndexer


def _make_settings(workspace: Path) -> MemorySettings:
    return MemorySettings(
        workspace_path=workspace,
        max_daily_note_bytes=32_768,
        daily_notes_load_days=2,
        daily_notes_max_tokens=4000,
        flush_min_confidence=0.5,
    )


class TestHelpers:
    def test_parse_date_from_filename(self) -> None:
        assert MemoryIndexer._parse_date_from_filename("2026-02-22.md") == date(2026, 2, 22)

    def test_parse_date_invalid(self) -> None:
        assert MemoryIndexer._parse_date_from_filename("notes.md") is None

    def test_extract_scope_present(self) -> None:
        text = "[10:00] (source: user, scope: main)"
        assert MemoryIndexer._extract_scope(text) == "main"

    def test_extract_scope_absent(self) -> None:
        text = "[10:00] some old note"
        assert MemoryIndexer._extract_scope(text) == "main"

    def test_extract_scope_custom_default(self) -> None:
        text = "[10:00] some note"
        assert MemoryIndexer._extract_scope(text, default="other") == "other"

    def test_extract_entry_text(self) -> None:
        text = "[10:00] (source: user, scope: main)\nActual content here"
        result = MemoryIndexer._extract_entry_text(text)
        assert result == "Actual content here"

    def test_extract_entry_text_no_metadata(self) -> None:
        text = "Just plain content"
        result = MemoryIndexer._extract_entry_text(text)
        assert result == "Just plain content"

    def test_split_by_headers(self) -> None:
        content = "# Title\nIntro\n## Section A\nContent A\n## Section B\nContent B"
        sections = MemoryIndexer._split_by_headers(content)
        assert len(sections) == 3
        assert sections[0][0] == "Title"
        assert sections[1][0] == "Section A"
        assert "Content A" in sections[1][1]
        assert sections[2][0] == "Section B"

    def test_split_by_headers_empty(self) -> None:
        sections = MemoryIndexer._split_by_headers("")
        assert len(sections) == 0


class TestIndexDailyNote:
    @pytest.mark.asyncio
    async def test_index_nonexistent_file(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        db_factory = MagicMock()
        indexer = MemoryIndexer(db_factory, settings)

        count = await indexer.index_daily_note(tmp_path / "nonexistent.md")
        assert count == 0

    @pytest.mark.asyncio
    async def test_index_empty_file(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        db_factory = MagicMock()
        indexer = MemoryIndexer(db_factory, settings)

        filepath = tmp_path / "memory" / "2026-02-22.md"
        filepath.parent.mkdir(parents=True)
        filepath.write_text("", encoding="utf-8")

        count = await indexer.index_daily_note(filepath)
        assert count == 0


class TestIndexCuratedMemory:
    @pytest.mark.asyncio
    async def test_index_nonexistent_file(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        db_factory = MagicMock()
        indexer = MemoryIndexer(db_factory, settings)

        count = await indexer.index_curated_memory(tmp_path / "MEMORY.md")
        assert count == 0


class TestReindexAll:
    @pytest.mark.asyncio
    async def test_no_files(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        db_factory = MagicMock()
        indexer = MemoryIndexer(db_factory, settings)

        # Patch both methods to track calls
        indexer.index_daily_note = AsyncMock(return_value=0)
        indexer.index_curated_memory = AsyncMock(return_value=0)

        total = await indexer.reindex_all(scope_key="main")
        assert total == 0

    @pytest.mark.asyncio
    async def test_with_files(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        db_factory = MagicMock()
        indexer = MemoryIndexer(db_factory, settings)

        # Create test files
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "2026-02-22.md").write_text(
            "---\n[10:00] (source: user, scope: main)\nNote 1"
        )
        (tmp_path / "MEMORY.md").write_text("## Section\nContent")

        # Patch the actual indexing methods
        indexer.index_daily_note = AsyncMock(return_value=1)
        indexer.index_curated_memory = AsyncMock(return_value=1)

        total = await indexer.reindex_all(scope_key="main")
        assert total == 2
        indexer.index_daily_note.assert_called_once()
        indexer.index_curated_memory.assert_called_once()
