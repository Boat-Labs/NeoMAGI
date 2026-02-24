"""Memory writer: append entries to workspace daily notes files.

Responsibilities:
- Append text to memory/YYYY-MM-DD.md (create if not exist)
- Process flush candidates from compaction → daily notes
- Enforce file size limits
- UTF-8 safe writes (CJK compatible)
- Carry scope_key metadata on every write (ADR 0034)
- Trigger incremental index after write (best-effort)
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from src.infra.errors import MemoryWriteError
from src.memory.contracts import ResolvedFlushCandidate

if TYPE_CHECKING:
    from src.config.settings import MemorySettings
    from src.memory.indexer import MemoryIndexer

logger = structlog.get_logger()


class MemoryWriter:
    """Write memory entries to workspace daily notes files."""

    def __init__(
        self,
        workspace_path: Path,
        settings: MemorySettings,
        indexer: MemoryIndexer | None = None,
    ) -> None:
        self._workspace_path = workspace_path
        self._settings = settings
        self._indexer = indexer

    async def append_daily_note(
        self,
        text: str,
        *,
        scope_key: str = "main",
        source: str = "user",
        target_date: date | None = None,
    ) -> Path:
        """Append a timestamped entry to daily note file.

        Format:
        ---
        [HH:MM] (source: {source}, scope: {scope_key})
        {text}

        Returns: path to the written file.
        Raises: MemoryWriteError if file exceeds max size.
        """
        today = target_date or date.today()
        filename = f"{today.isoformat()}.md"
        memory_dir = self._workspace_path / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        filepath = memory_dir / filename

        now = datetime.now(UTC)
        entry = (
            f"---\n"
            f"[{now.strftime('%H:%M')}] (source: {source}, scope: {scope_key})\n"
            f"{text}\n"
        )

        # Check file size limit before writing
        current_size = filepath.stat().st_size if filepath.exists() else 0
        entry_bytes = entry.encode("utf-8")
        if current_size + len(entry_bytes) > self._settings.max_daily_note_bytes:
            logger.warning(
                "daily_note_size_limit",
                path=str(filepath),
                current_size=current_size,
                entry_size=len(entry_bytes),
                max_bytes=self._settings.max_daily_note_bytes,
            )
            raise MemoryWriteError(
                f"Daily note {filename} would exceed size limit "
                f"({current_size + len(entry_bytes)} > {self._settings.max_daily_note_bytes})"
            )

        # Append to file (create if not exist)
        with filepath.open("a", encoding="utf-8") as f:
            f.write(entry)

        logger.info(
            "daily_note_appended",
            path=str(filepath),
            scope_key=scope_key,
            source=source,
            bytes_written=len(entry_bytes),
        )

        # Incremental index: best-effort, failure must not block write path
        if self._indexer:
            try:
                rel_path = str(filepath.relative_to(self._workspace_path))
                await self._indexer.index_entry_direct(
                    content=text,
                    scope_key=scope_key,
                    source_type="daily_note",
                    source_path=rel_path,
                    source_date=today,
                )
            except Exception:
                logger.warning(
                    "memory_index_after_write_failed",
                    path=str(filepath),
                    scope_key=scope_key,
                )

        return filepath

    async def process_flush_candidates(
        self,
        candidates: list[ResolvedFlushCandidate],
        *,
        min_confidence: float = 0.5,
    ) -> int:
        """Filter and persist flush candidates to today's daily note.

        Filters:
        - candidate.confidence >= min_confidence
        - candidate.candidate_text non-empty

        Returns: number of candidates written.
        """
        written = 0
        for candidate in candidates:
            if candidate.confidence < min_confidence:
                logger.debug(
                    "flush_candidate_filtered",
                    confidence=candidate.confidence,
                    min_confidence=min_confidence,
                )
                continue
            if not candidate.candidate_text.strip():
                continue

            try:
                await self.append_daily_note(
                    text=candidate.candidate_text,
                    scope_key=candidate.scope_key,
                    source="compaction_flush",
                )
                written += 1
            except MemoryWriteError:
                logger.warning(
                    "flush_candidate_write_failed",
                    scope_key=candidate.scope_key,
                    source_session_id=candidate.source_session_id,
                )
                break  # Stop writing if file limit reached

        logger.info(
            "flush_candidates_processed",
            total=len(candidates),
            written=written,
        )
        return written
