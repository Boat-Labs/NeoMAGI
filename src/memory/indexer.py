"""Memory indexer: sync memory files to PostgreSQL search index.

Source of truth: files (daily notes + MEMORY.md).
Index: memory_entries table (for search only).
All indexed entries carry scope_key for scope-aware filtering (ADR 0034).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.memory.models import MemoryEntry

if TYPE_CHECKING:
    from src.config.settings import MemorySettings

logger = structlog.get_logger()


class MemoryIndexer:
    """Sync memory files to PostgreSQL search index.

    All indexed entries carry scope_key for scope-aware filtering (ADR 0034).
    Strategy: delete-reinsert (idempotent) — files are source of truth.
    """

    def __init__(
        self,
        db_session_factory: async_sessionmaker[AsyncSession],
        settings: MemorySettings,
    ) -> None:
        self._db_factory = db_session_factory
        self._settings = settings

    async def index_daily_note(self, file_path: Path, *, scope_key: str = "main") -> int:
        """Parse and index a daily note file (idempotent delete-reinsert)."""
        if not file_path.is_file():
            return 0

        content = file_path.read_text(encoding="utf-8").strip()
        if not content:
            return 0

        source_date = self._parse_date_from_filename(file_path.name)
        rel_path = self._relative_path(file_path)
        entries = re.split(r"^---$", content, flags=re.MULTILINE)
        rows = self._parse_daily_entries(entries, scope_key, source_date, rel_path)
        await self._persist_entries(rows, rel_path)

        logger.info("daily_note_indexed", path=rel_path, entries=len(rows),
                     scope_key=scope_key)
        return len(rows)

    async def index_curated_memory(self, file_path: Path, *, scope_key: str = "main") -> int:
        """Parse and index MEMORY.md by markdown headers.

        Each ## section becomes one memory_entries row with source_type='curated'.
        """
        if not file_path.is_file():
            return 0

        content = file_path.read_text(encoding="utf-8").strip()
        if not content:
            return 0

        rel_path = self._relative_path(file_path)
        sections = self._split_by_headers(content)

        async with self._db_factory() as db:
            await db.execute(delete(MemoryEntry).where(MemoryEntry.source_path == rel_path))

            for title, body in sections:
                if not body.strip():
                    continue
                entry = MemoryEntry(
                    scope_key=scope_key,
                    source_type="curated",
                    source_path=rel_path,
                    source_date=None,
                    title=title,
                    content=body.strip(),
                    tags=[],
                    confidence=None,
                )
                db.add(entry)

            await db.commit()

        logger.info(
            "curated_memory_indexed",
            path=rel_path,
            sections=len(sections),
            scope_key=scope_key,
        )
        return len(sections)

    async def reindex_all(self, *, scope_key: str = "main") -> int:
        """Full reindex: scan workspace/memory/ + MEMORY.md."""
        total = 0
        workspace = self._settings.workspace_path
        memory_dir = workspace / "memory"

        if memory_dir.is_dir():
            for filepath in sorted(memory_dir.glob("*.md")):
                count = await self.index_daily_note(filepath, scope_key=scope_key)
                total += count

        memory_md = workspace / "MEMORY.md"
        if memory_md.is_file():
            count = await self.index_curated_memory(memory_md, scope_key=scope_key)
            total += count

        logger.info("reindex_complete", total_entries=total, scope_key=scope_key)
        return total

    async def index_entry_direct(
        self,
        *,
        content: str,
        scope_key: str,
        source_type: str = "daily_note",
        source_path: str | None = None,
        source_date: date | None = None,
        title: str = "",
        tags: list[str] | None = None,
        confidence: float | None = None,
    ) -> int:
        """Index a single entry directly (used by writer for incremental index)."""
        async with self._db_factory() as db:
            entry = MemoryEntry(
                scope_key=scope_key,
                source_type=source_type,
                source_path=source_path,
                source_date=source_date,
                title=title,
                content=content,
                tags=tags or [],
                confidence=confidence,
            )
            db.add(entry)
            await db.commit()
        return 1

    def _parse_daily_entries(
        self, entries: list[str], scope_key: str,
        source_date: date | None, rel_path: str,
    ) -> list[dict]:
        """Parse daily note entries into row dicts for DB insertion."""
        rows: list[dict] = []
        for entry in entries:
            stripped = entry.strip()
            if not stripped:
                continue
            entry_scope = self._extract_scope(stripped, default=scope_key)
            entry_text = self._extract_entry_text(stripped)
            if not entry_text:
                continue
            rows.append({
                "scope_key": entry_scope, "source_type": "daily_note",
                "source_path": rel_path, "source_date": source_date,
                "title": "", "content": entry_text, "tags": [], "confidence": None,
            })
        return rows

    async def _persist_entries(self, rows: list[dict], rel_path: str) -> None:
        """Delete-reinsert entries for a single source path."""
        async with self._db_factory() as db:
            await db.execute(delete(MemoryEntry).where(MemoryEntry.source_path == rel_path))
            for row in rows:
                db.add(MemoryEntry(**row))
            await db.commit()

    @staticmethod
    def _parse_date_from_filename(filename: str) -> date | None:
        """Extract date from YYYY-MM-DD.md filename."""
        match = re.match(r"(\d{4}-\d{2}-\d{2})\.md$", filename)
        if match:
            try:
                return date.fromisoformat(match.group(1))
            except ValueError:
                return None
        return None

    @staticmethod
    def _extract_scope(entry_text: str, *, default: str = "main") -> str:
        """Extract scope from entry metadata line.

        Old data compatibility: no scope → return default (='main').
        """
        match = re.search(r"scope:\s*(\S+)", entry_text)
        if match:
            return match.group(1).rstrip(")")
        return default

    @staticmethod
    def _extract_entry_text(entry_text: str) -> str:
        """Extract content from daily note entry, skipping metadata line."""
        lines = entry_text.split("\n")
        # First line is metadata: [HH:MM] (source: ..., scope: ...)
        # Content starts from second line
        content_lines = []
        for line in lines:
            if re.match(r"^\[[\d:]+\]", line):
                continue  # skip metadata line
            content_lines.append(line)
        return "\n".join(content_lines).strip()

    def _relative_path(self, file_path: Path) -> str:
        """Convert absolute path to relative workspace path."""
        try:
            return str(file_path.relative_to(self._settings.workspace_path))
        except ValueError:
            return str(file_path)

    @staticmethod
    def _split_by_headers(content: str) -> list[tuple[str, str]]:
        """Split markdown content by ## headers into (title, body) pairs."""
        if not content.strip():
            return []

        sections: list[tuple[str, str]] = []
        current_title = ""
        current_body: list[str] = []

        for line in content.split("\n"):
            if line.startswith("## "):
                if current_title or current_body:
                    sections.append((current_title, "\n".join(current_body)))
                current_title = line[3:].strip()
                current_body = []
            elif line.startswith("# ") and not current_title:
                current_title = line[2:].strip()
                current_body = []
            else:
                current_body.append(line)

        if current_title or current_body:
            sections.append((current_title, "\n".join(current_body)))

        return sections
