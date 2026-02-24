"""Integration tests for memory index + search (tsvector BM25 fallback).

Requires PostgreSQL (via testcontainers or TEST_DATABASE_* env vars).
Tests the full write → index → search cycle with scope isolation.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text

from src.config.settings import MemorySettings
from src.constants import DB_SCHEMA
from src.memory.indexer import MemoryIndexer
from src.memory.searcher import MemorySearcher
from src.memory.writer import MemoryWriter

pytestmark = pytest.mark.integration


def _make_settings(workspace: Path) -> MemorySettings:
    return MemorySettings(
        workspace_path=workspace,
        max_daily_note_bytes=32_768,
        daily_notes_load_days=2,
        daily_notes_max_tokens=4000,
        flush_min_confidence=0.5,
    )


@pytest_asyncio.fixture()
async def memory_db(db_engine, db_session_factory):
    """Set up memory_entries trigger and provide shared session factory.

    Reuses the session-scoped db_engine/db_session_factory from conftest
    to avoid creating separate engines that corrupt the event loop.
    """
    async with db_engine.begin() as conn:
        # Create the search trigger (idempotent via CREATE OR REPLACE)
        await conn.execute(text(f"""
            CREATE OR REPLACE FUNCTION {DB_SCHEMA}.memory_entries_search_trigger()
            RETURNS trigger AS $$
            BEGIN
                NEW.search_vector :=
                    setweight(to_tsvector('simple', COALESCE(NEW.title, '')), 'A') ||
                    setweight(to_tsvector('simple', COALESCE(NEW.content, '')), 'B');
                NEW.updated_at := now();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
        """))

        # Check if trigger exists before creating
        result = await conn.execute(text(f"""
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'trg_memory_entries_search'
            AND tgrelid = '{DB_SCHEMA}.memory_entries'::regclass
        """))
        if result.fetchone() is None:
            await conn.execute(text(f"""
                CREATE TRIGGER trg_memory_entries_search
                BEFORE INSERT OR UPDATE ON {DB_SCHEMA}.memory_entries
                FOR EACH ROW
                EXECUTE FUNCTION {DB_SCHEMA}.memory_entries_search_trigger();
            """))

    yield db_session_factory

    async with db_session_factory() as db:
        await db.execute(text(f"TRUNCATE {DB_SCHEMA}.memory_entries CASCADE"))
        await db.commit()


class TestWriteIndexSearchCycle:
    """Full cycle: write → index → search."""

    @pytest.mark.asyncio
    async def test_write_index_search(self, memory_db, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        indexer = MemoryIndexer(memory_db, settings)
        searcher = MemorySearcher(memory_db, settings)

        # Write a daily note file
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir(parents=True)
        filepath = memory_dir / "2026-02-22.md"
        filepath.write_text(
            "---\n[10:00] (source: user, scope: main)\n"
            "User prefers dark mode for all applications\n"
            "---\n[11:00] (source: user, scope: main)\n"
            "Project uses PostgreSQL 16\n",
            encoding="utf-8",
        )

        # Index
        count = await indexer.index_daily_note(filepath, scope_key="main")
        assert count == 2

        # Search
        results = await searcher.search("dark mode", scope_key="main")
        assert len(results) >= 1
        assert any("dark mode" in r.content.lower() for r in results)

    @pytest.mark.asyncio
    async def test_chinese_search(self, memory_db, tmp_path: Path) -> None:
        """Test CJK content indexing and search."""
        settings = _make_settings(tmp_path)
        indexer = MemoryIndexer(memory_db, settings)
        searcher = MemorySearcher(memory_db, settings)

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir(parents=True)
        filepath = memory_dir / "2026-02-22.md"
        filepath.write_text(
            "---\n[10:00] (source: user, scope: main)\n"
            "用户偏好 中文回复 和 dark mode 界面\n",
            encoding="utf-8",
        )

        count = await indexer.index_daily_note(filepath, scope_key="main")
        assert count == 1

        # Search with English part should work
        results = await searcher.search("dark mode", scope_key="main")
        assert len(results) >= 1


class TestIdempotentReindex:
    @pytest.mark.asyncio
    async def test_reindex_does_not_duplicate(self, memory_db, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        indexer = MemoryIndexer(memory_db, settings)

        memory_dir = tmp_path / "memory"
        memory_dir.mkdir(parents=True)
        filepath = memory_dir / "2026-02-22.md"
        filepath.write_text(
            "---\n[10:00] (source: user, scope: main)\nNote content\n",
            encoding="utf-8",
        )

        count1 = await indexer.index_daily_note(filepath, scope_key="main")
        count2 = await indexer.index_daily_note(filepath, scope_key="main")

        # Should return same count (not accumulate)
        assert count1 == count2

        # Verify actual row count
        async with memory_db() as db:
            result = await db.execute(
                text(f"SELECT COUNT(*) FROM {DB_SCHEMA}.memory_entries")
            )
            total = result.scalar()
            assert total == count1  # No duplicates


class TestScopeIsolation:
    @pytest.mark.asyncio
    async def test_different_scopes_not_visible(self, memory_db, tmp_path: Path) -> None:
        """Entries with different scope_keys must not be visible to each other."""
        settings = _make_settings(tmp_path)
        indexer = MemoryIndexer(memory_db, settings)
        searcher = MemorySearcher(memory_db, settings)

        # Index entry directly with scope_key="main"
        await indexer.index_entry_direct(
            content="Main scope secret data",
            scope_key="main",
            source_type="daily_note",
        )

        # Index entry directly with scope_key="other"
        await indexer.index_entry_direct(
            content="Other scope secret data",
            scope_key="other",
            source_type="daily_note",
        )

        # Search from main scope
        main_results = await searcher.search("secret data", scope_key="main")
        other_results = await searcher.search("secret data", scope_key="other")

        # Each scope should only see its own data
        assert all(r.scope_key == "main" for r in main_results)
        assert all(r.scope_key == "other" for r in other_results)

        if main_results:
            assert "Main scope" in main_results[0].content
        if other_results:
            assert "Other scope" in other_results[0].content


class TestCuratedMemoryIndex:
    @pytest.mark.asyncio
    async def test_index_memory_md(self, memory_db, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        indexer = MemoryIndexer(memory_db, settings)
        searcher = MemorySearcher(memory_db, settings)

        memory_md = tmp_path / "MEMORY.md"
        memory_md.write_text(
            "## User Preferences\nPrefers dark mode and concise responses\n\n"
            "## Technical Stack\nProject uses PostgreSQL 16 with pgvector\n",
            encoding="utf-8",
        )

        count = await indexer.index_curated_memory(memory_md, scope_key="main")
        assert count == 2

        results = await searcher.search("PostgreSQL", scope_key="main")
        assert len(results) >= 1


class TestWriterIncrementalIndex:
    @pytest.mark.asyncio
    async def test_writer_triggers_index(self, memory_db, tmp_path: Path) -> None:
        """Writer with indexer should auto-index after write."""
        settings = _make_settings(tmp_path)
        indexer = MemoryIndexer(memory_db, settings)
        searcher = MemorySearcher(memory_db, settings)
        writer = MemoryWriter(tmp_path, settings, indexer=indexer)

        target_date = date(2026, 2, 22)
        await writer.append_daily_note(
            "Remember to always use dark mode",
            scope_key="main",
            source="user",
            target_date=target_date,
        )

        # Search should find the indexed entry
        results = await searcher.search("dark mode", scope_key="main")
        assert len(results) >= 1
