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

    async def reindex_all(
        self, *, scope_key: str | None = "main", ledger: object | None = None,
    ) -> int:
        """Full reindex: daily_note from ledger or workspace, curated from files.

        P2-M3b: when ledger is provided, daily_note entries are rebuilt from
        the DB ledger current view (with principal_id + visibility).
        Curated memory (MEMORY.md) always reindexes from workspace files.

        scope_key=None with ledger: rebuilds all scopes from ledger (used by restore).
        scope_key=None without ledger: rebuilds all workspace files (no scope filter).
        """
        total = 0
        workspace = self._settings.workspace_path

        if ledger is not None:
            # Ledger-based reindex for daily_note entries (scope_key=None → all scopes)
            count = await self.reindex_from_ledger(ledger, scope_key=scope_key)
            total += count
        else:
            # Workspace-based fallback for daily_note entries
            memory_dir = workspace / "memory"
            if memory_dir.is_dir():
                ws_scope = scope_key or "main"
                for filepath in sorted(memory_dir.glob("*.md")):
                    count = await self.index_daily_note(filepath, scope_key=ws_scope)
                    total += count

        # Curated memory always from workspace files
        curated_scope = scope_key or "main"
        memory_md = workspace / "MEMORY.md"
        if memory_md.is_file():
            count = await self.index_curated_memory(memory_md, scope_key=curated_scope)
            total += count

        logger.info("reindex_complete", total_entries=total, scope_key=scope_key,
                     ledger_based=ledger is not None)
        return total

    async def reindex_from_ledger(
        self, ledger: object, *, scope_key: str | None = None,
    ) -> int:
        """Rebuild memory_entries from ledger current view (ADR 0060, P2-M3b).

        Deletes daily_note entries and re-inserts from ledger. Curated entries
        are preserved (handled separately by reindex_all).
        """
        entries = await ledger.get_current_view(scope_key=scope_key)

        async with self._db_factory() as db:
            # Delete daily_note entries (curated entries preserved)
            del_stmt = delete(MemoryEntry).where(
                MemoryEntry.source_type == "daily_note"
            )
            if scope_key is not None:
                del_stmt = del_stmt.where(MemoryEntry.scope_key == scope_key)
            await db.execute(del_stmt)

            for e in entries:
                source_date = e["created_at"].date() if e.get("created_at") else None
                entry = MemoryEntry(
                    entry_id=e["entry_id"],
                    scope_key=e["scope_key"],
                    source_type="daily_note",
                    source_path=None,
                    source_date=source_date,
                    source_session_id=e.get("source_session_id"),
                    principal_id=e.get("principal_id"),
                    visibility=e.get("visibility", "private_to_principal"),
                    title="",
                    content=e["content"],
                    tags=[],
                    confidence=None,
                )
                db.add(entry)

            await db.commit()

        logger.info("reindex_from_ledger_complete", entries=len(entries),
                     scope_key=scope_key)
        return len(entries)

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
        entry_id: str | None = None,
        source_session_id: str | None = None,
        principal_id: str | None = None,
        visibility: str = "private_to_principal",
    ) -> int:
        """Index a single entry directly (used by writer for incremental index)."""
        async with self._db_factory() as db:
            entry = MemoryEntry(
                entry_id=entry_id,
                scope_key=scope_key,
                source_type=source_type,
                source_path=source_path,
                source_date=source_date,
                title=title,
                content=content,
                tags=tags or [],
                confidence=confidence,
                source_session_id=source_session_id,
                principal_id=principal_id,
                visibility=visibility,
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
            meta = self._parse_entry_metadata(stripped, default_scope=scope_key)
            entry_text = self._extract_entry_text(stripped)
            if not entry_text:
                continue
            rows.append({
                "entry_id": meta["entry_id"],
                "scope_key": meta["scope"], "source_type": "daily_note",
                "source_path": rel_path, "source_date": source_date,
                "source_session_id": meta["source_session_id"],
                "principal_id": meta.get("principal"),
                "visibility": meta.get("visibility", "private_to_principal"),
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
    def _parse_entry_metadata(
        entry_text: str, *, default_scope: str = "main",
    ) -> dict[str, str | None]:
        """Extract metadata fields from a daily note entry's first metadata line only.

        Only the first line matching ``[HH:MM]`` is considered; body content
        is never scanned, preventing false positives from user prose.

        Returns dict with keys: entry_id, source, scope, source_session_id,
        principal, visibility.
        Missing fields resolve to None (entry_id, source, source_session_id, principal)
        or default_scope (scope) or "private_to_principal" (visibility).
        Backward-compatible with old format.
        """
        first_line = entry_text.split("\n", 1)[0]
        if not re.match(r"^\[[\d:]+\]", first_line):
            return {
                "entry_id": None, "source": None,
                "scope": default_scope, "source_session_id": None,
                "principal": None, "visibility": "private_to_principal",
            }
        entry_id_m = re.search(r"entry_id:\s*(\S+)", first_line)
        source_m = re.search(r"source:\s*(\S+)", first_line)
        scope_m = re.search(r"scope:\s*(\S+)", first_line)
        ssid_m = re.search(r"source_session_id:\s*(\S+)", first_line)
        principal_m = re.search(r"principal:\s*(\S+)", first_line)
        visibility_m = re.search(r"visibility:\s*(\S+)", first_line)
        return {
            "entry_id": entry_id_m.group(1).rstrip(",)") if entry_id_m else None,
            "source": source_m.group(1).rstrip(",)") if source_m else None,
            "scope": scope_m.group(1).rstrip(",)") if scope_m else default_scope,
            "source_session_id": ssid_m.group(1).rstrip(",)") if ssid_m else None,
            "principal": principal_m.group(1).rstrip(",)") if principal_m else None,
            "visibility": (
                visibility_m.group(1).rstrip(",)") if visibility_m
                else "private_to_principal"
            ),
        }

    @staticmethod
    def _extract_scope(entry_text: str, *, default: str = "main") -> str:
        """Extract scope from entry metadata line.

        Old data compatibility: no scope → return default (='main').
        """
        match = re.search(r"scope:\s*(\S+)", entry_text)
        if match:
            return match.group(1).rstrip(",)")
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
