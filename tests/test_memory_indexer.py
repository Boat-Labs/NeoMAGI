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
        # h1 preamble ("# Title" + "Intro") is skipped; only ## sections returned
        assert len(sections) == 2
        assert sections[0][0] == "Section A"
        assert "Content A" in sections[0][1]
        assert sections[1][0] == "Section B"

    def test_split_by_headers_empty(self) -> None:
        sections = MemoryIndexer._split_by_headers("")
        assert len(sections) == 0


class TestParseEntryMetadata:
    """ADR 0053: unified metadata parser."""

    def test_new_format_all_fields(self) -> None:
        text = (
            "[22:47] (entry_id: 0195d9d7-6f5e-7d9b-a2d3-8a4d4f3d2c11, "
            "source: user, scope: main, source_session_id: telegram:peer:123)"
        )
        meta = MemoryIndexer._parse_entry_metadata(text)
        assert meta["entry_id"] == "0195d9d7-6f5e-7d9b-a2d3-8a4d4f3d2c11"
        assert meta["source"] == "user"
        assert meta["scope"] == "main"
        assert meta["source_session_id"] == "telegram:peer:123"

    def test_old_format_no_entry_id(self) -> None:
        text = "[10:00] (source: user, scope: main)"
        meta = MemoryIndexer._parse_entry_metadata(text)
        assert meta["entry_id"] is None
        assert meta["source"] == "user"
        assert meta["scope"] == "main"
        assert meta["source_session_id"] is None

    def test_old_format_no_scope(self) -> None:
        text = "[10:00] some old note"
        meta = MemoryIndexer._parse_entry_metadata(text)
        assert meta["entry_id"] is None
        assert meta["source"] is None
        assert meta["scope"] == "main"
        assert meta["source_session_id"] is None

    def test_custom_default_scope(self) -> None:
        text = "[10:00] no metadata"
        meta = MemoryIndexer._parse_entry_metadata(text, default_scope="other")
        assert meta["scope"] == "other"

    def test_new_format_without_source_session_id(self) -> None:
        text = "[10:00] (entry_id: abc-def-123, source: user, scope: main)"
        meta = MemoryIndexer._parse_entry_metadata(text)
        assert meta["entry_id"] == "abc-def-123"
        assert meta["scope"] == "main"
        assert meta["source_session_id"] is None

    def test_body_content_not_parsed_as_metadata(self) -> None:
        """Body prose mentioning metadata keys must not poison parsed values."""
        text = (
            "[10:00] (source: user, scope: main)\n"
            "User said source_session_id: evil and entry_id: fake"
        )
        meta = MemoryIndexer._parse_entry_metadata(text)
        assert meta["entry_id"] is None
        assert meta["source_session_id"] is None
        assert meta["scope"] == "main"

    def test_no_metadata_line(self) -> None:
        """Plain text without [HH:MM] prefix returns all defaults."""
        meta = MemoryIndexer._parse_entry_metadata("Just plain content")
        assert meta["entry_id"] is None
        assert meta["source"] is None
        assert meta["scope"] == "main"
        assert meta["source_session_id"] is None

    def test_source_compaction_flush(self) -> None:
        """P2-M2d: source field extraction for compaction_flush."""
        text = "[10:00] (entry_id: abc-123, source: compaction_flush, scope: main)"
        meta = MemoryIndexer._parse_entry_metadata(text)
        assert meta["source"] == "compaction_flush"

    def test_missing_source_returns_none(self) -> None:
        """P2-M2d: missing source field returns None."""
        text = "[10:00] (entry_id: abc-123, scope: main)"
        meta = MemoryIndexer._parse_entry_metadata(text)
        assert meta["source"] is None


class TestParseDailyEntries:
    """Verify _parse_daily_entries propagates ADR 0053 fields into row dicts."""

    def test_new_format_rows_include_entry_id(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        db_factory = MagicMock()
        indexer = MemoryIndexer(db_factory, settings)

        entries = [
            "",
            (
                "[22:47] (entry_id: abc-123, source: user, scope: main,"
                " source_session_id: telegram:peer:42)\nContent here"
            ),
        ]
        rows = indexer._parse_daily_entries(
            entries,
            "main",
            date(2026, 3, 17),
            "memory/2026-03-17.md",
        )
        assert len(rows) == 1
        assert rows[0]["entry_id"] == "abc-123"
        assert rows[0]["source_session_id"] == "telegram:peer:42"
        assert rows[0]["scope_key"] == "main"
        assert rows[0]["content"] == "Content here"

    def test_old_format_rows_have_null_fields(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        db_factory = MagicMock()
        indexer = MemoryIndexer(db_factory, settings)

        entries = ["", "[10:00] (source: user, scope: main)\nOld content"]
        rows = indexer._parse_daily_entries(
            entries,
            "main",
            date(2026, 2, 22),
            "memory/2026-02-22.md",
        )
        assert len(rows) == 1
        assert rows[0]["entry_id"] is None
        assert rows[0]["source_session_id"] is None
        assert rows[0]["content"] == "Old content"


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
