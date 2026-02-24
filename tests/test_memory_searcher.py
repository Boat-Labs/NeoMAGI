"""Tests for MemorySearcher (unit tests with mocked DB).

Integration tests with real DB are in tests/integration/test_memory_bm25.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.config.settings import MemorySettings
from src.memory.searcher import MemorySearcher, MemorySearchResult


def _make_settings() -> MemorySettings:
    return MemorySettings(
        workspace_path=Path("workspace"),
        max_daily_note_bytes=32_768,
        daily_notes_load_days=2,
        daily_notes_max_tokens=4000,
        flush_min_confidence=0.5,
    )


class TestMemorySearchResult:
    def test_create(self) -> None:
        r = MemorySearchResult(
            entry_id=1,
            scope_key="main",
            source_type="daily_note",
            source_path="memory/2026-02-22.md",
            title="",
            content="test content",
            score=0.5,
            tags=["user_preference"],
            created_at=datetime.now(UTC),
        )
        assert r.entry_id == 1
        assert r.scope_key == "main"
        assert r.score == 0.5


class TestMemorySearcher:
    @pytest.mark.asyncio
    async def test_empty_query_returns_empty(self) -> None:
        db_factory = MagicMock()
        settings = _make_settings()
        searcher = MemorySearcher(db_factory, settings)

        results = await searcher.search("", scope_key="main")
        assert results == []

    @pytest.mark.asyncio
    async def test_whitespace_query_returns_empty(self) -> None:
        db_factory = MagicMock()
        settings = _make_settings()
        searcher = MemorySearcher(db_factory, settings)

        results = await searcher.search("   ", scope_key="main")
        assert results == []
