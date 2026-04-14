"""Memory writer: append entries to workspace daily notes files.

Dual-mode writer (P2-M2d, ADR 0060):
- Ledger-wired mode (production): DB ledger is truth, workspace projection best-effort.
- No-ledger fallback mode (tests/legacy): workspace projection is mandatory.

Responsibilities:
- Append text to memory/YYYY-MM-DD.md (create if not exist)
- Append events to DB memory_source_ledger (when ledger is wired)
- Process flush candidates from compaction → daily notes
- Enforce file size limits (projection-only; never blocks ledger truth)
- UTF-8 safe writes (CJK compatible)
- Carry scope_key metadata on every write (ADR 0034)
- Trigger incremental index after write (best-effort)
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from src.infra.errors import LedgerWriteError, MemoryWriteError, VisibilityPolicyError
from src.memory.contracts import ResolvedFlushCandidate
from src.memory.visibility import MemoryPolicyEntry, PolicyContext, can_write

if TYPE_CHECKING:
    from src.config.settings import MemorySettings
    from src.memory.indexer import MemoryIndexer
    from src.memory.ledger import MemoryLedgerWriter

logger = structlog.get_logger()

_uuid7_last_ms: int = 0
_uuid7_seq: int = 0


def _uuid7() -> uuid.UUID:
    """Generate a UUIDv7 (time-ordered, monotonic within ms). Minimal local helper (ADR 0053).

    Uses a 12-bit monotonic counter (rand_a) within the same millisecond to
    guarantee strict ordering for back-to-back calls.  The counter resets to a
    random base on each new millisecond tick.
    """
    global _uuid7_last_ms, _uuid7_seq  # noqa: PLW0603
    timestamp_ms = int(time.time() * 1000)
    if timestamp_ms <= _uuid7_last_ms:
        _uuid7_seq += 1
        timestamp_ms = _uuid7_last_ms
    else:
        _uuid7_last_ms = timestamp_ms
        _uuid7_seq = int.from_bytes(os.urandom(2), "big") & 0x0FFF
    seq = _uuid7_seq & 0x0FFF
    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF
    uuid_int = (timestamp_ms << 80) | (0x7 << 76) | (seq << 64) | (0x2 << 62) | rand_b
    return uuid.UUID(int=uuid_int)


@dataclass(frozen=True)
class MemoryWriteResult:
    """Result of append_daily_note(), reflecting write mode semantics."""

    entry_id: str
    ledger_written: bool  # True only when ledger INSERT succeeded (not idempotent no-op)
    projection_written: bool  # True when daily note file written
    projection_path: Path | None  # None when projection skipped/failed


class MemoryWriter:
    """Write memory entries to workspace daily notes files.

    Dual-mode writer (P2-M2d):
    - Ledger-wired mode: DB ledger truth-first, workspace projection best-effort.
    - No-ledger fallback mode: workspace projection mandatory (preserves pre-M2d behavior).
    """

    def __init__(
        self,
        workspace_path: Path,
        settings: MemorySettings,
        indexer: MemoryIndexer | None = None,
        ledger: MemoryLedgerWriter | None = None,
    ) -> None:
        self._workspace_path = workspace_path
        self._settings = settings
        self._indexer = indexer
        self._ledger = ledger

    async def append_daily_note(
        self,
        text: str,
        *,
        scope_key: str = "main",
        source: str = "user",
        source_session_id: str | None = None,
        target_date: date | None = None,
        principal_id: str | None = None,
        visibility: str = "private_to_principal",
    ) -> MemoryWriteResult:
        """Append a timestamped entry to daily note file and/or DB ledger.

        Ledger-wired mode: writes ledger first (truth), then projection (best-effort).
        No-ledger fallback: writes projection only (mandatory, raises on failure).

        P2-M3c: uses can_write() policy check instead of ad-hoc visibility constants.
        VisibilityPolicyError inherits MemoryWriteError for catch-site compatibility.

        Returns: MemoryWriteResult with write outcome details.
        Raises: VisibilityPolicyError if visibility policy denies the write.
        Raises: LedgerWriteError if ledger write fails (ledger-wired mode).
        Raises: MemoryWriteError if projection write fails.
        """
        # V1: owner = requester
        policy_entry = MemoryPolicyEntry(
            entry_id="pending",
            owner_principal_id=principal_id,
            visibility=visibility,
            scope_key=scope_key,
        )
        ctx = PolicyContext(principal_id=principal_id, scope_key=scope_key)
        decision = can_write(ctx, policy_entry)
        if not decision.allowed:
            logger.info(
                "visibility_policy_denied",
                principal_id=principal_id, visibility=visibility,
                scope_key=scope_key, reason=decision.reason,
            )
            raise VisibilityPolicyError(decision.reason)

        today = target_date or date.today()
        filename = f"{today.isoformat()}.md"
        filepath = self._workspace_path / "memory" / filename

        entry_id = str(_uuid7())
        now = datetime.now(UTC)
        meta_parts = [
            f"entry_id: {entry_id}",
            f"source: {source}",
            f"scope: {scope_key}",
        ]
        if principal_id is not None:
            meta_parts.append(f"principal: {principal_id}")
        meta_parts.append(f"visibility: {visibility}")
        if source_session_id:
            meta_parts.append(f"source_session_id: {source_session_id}")
        meta_line = f"[{now.strftime('%H:%M')}] ({', '.join(meta_parts)})"
        entry = f"---\n{meta_line}\n{text}\n"
        entry_bytes = entry.encode("utf-8")

        if self._ledger:
            # ── Ledger-wired mode: truth-first ──
            ledger_written = await self._ledger.append(
                entry_id=entry_id, content=text, scope_key=scope_key,
                source=source, source_session_id=source_session_id,
                principal_id=principal_id, visibility=visibility,
            )  # LedgerWriteError propagates

            # Idempotent no-op: ledger already has this entry_id → skip projection
            if not ledger_written:
                return MemoryWriteResult(
                    entry_id=entry_id, ledger_written=False,
                    projection_written=False, projection_path=None,
                )

            # Workspace projection (best-effort)
            projection_written = self._try_write_projection(filepath, entry, entry_bytes)
        else:
            # ── No-ledger fallback mode: projection mandatory ──
            ledger_written = False
            filepath.parent.mkdir(parents=True, exist_ok=True)
            self._check_size_limit(filepath, entry_bytes, filename)  # raises MemoryWriteError
            with filepath.open("a", encoding="utf-8") as f:
                f.write(entry)
            projection_written = True

        if projection_written:
            logger.info(
                "daily_note_appended", path=str(filepath), entry_id=entry_id,
                scope_key=scope_key, source=source, bytes_written=len(entry_bytes),
            )

        # Incremental index: driven by ledger_written in ledger-wired mode (D13),
        # by projection_written in no-ledger fallback mode.
        should_index = ledger_written if self._ledger else projection_written
        if should_index:
            source_path = filepath if projection_written else None
            await self._try_incremental_index(
                source_path, text, scope_key, today,
                entry_id=entry_id, source_session_id=source_session_id,
                principal_id=principal_id, visibility=visibility,
            )

        return MemoryWriteResult(
            entry_id=entry_id,
            ledger_written=ledger_written,
            projection_written=projection_written,
            projection_path=filepath if projection_written else None,
        )

    def _try_write_projection(
        self, filepath: Path, entry: str, entry_bytes: bytes,
    ) -> bool:
        """Best-effort workspace projection write. Only used in ledger-wired mode.

        Never raises. Size limit exceeded → skip + warning.
        """
        try:
            current_size = filepath.stat().st_size if filepath.exists() else 0
            if current_size + len(entry_bytes) > self._settings.max_daily_note_bytes:
                logger.warning(
                    "daily_note_projection_size_limit",
                    path=str(filepath), current_size=current_size,
                    entry_size=len(entry_bytes),
                    max_bytes=self._settings.max_daily_note_bytes,
                )
                return False
            filepath.parent.mkdir(parents=True, exist_ok=True)
            with filepath.open("a", encoding="utf-8") as f:
                f.write(entry)
            return True
        except OSError:
            logger.warning("daily_note_projection_write_failed", path=str(filepath))
            return False

    def _check_size_limit(
        self, filepath: Path, entry_bytes: bytes, filename: str,
    ) -> None:
        """Raise MemoryWriteError if appending would exceed daily note size limit.

        Only used in no-ledger fallback mode (preserves pre-M2d behavior).
        """
        current_size = filepath.stat().st_size if filepath.exists() else 0
        if current_size + len(entry_bytes) > self._settings.max_daily_note_bytes:
            logger.warning(
                "daily_note_size_limit", path=str(filepath),
                current_size=current_size, entry_size=len(entry_bytes),
                max_bytes=self._settings.max_daily_note_bytes,
            )
            raise MemoryWriteError(
                f"Daily note {filename} would exceed size limit "
                f"({current_size + len(entry_bytes)} > {self._settings.max_daily_note_bytes})"
            )

    async def _try_incremental_index(
        self, filepath: Path | None, text: str, scope_key: str, today: date,
        *, entry_id: str | None = None, source_session_id: str | None = None,
        principal_id: str | None = None, visibility: str = "private_to_principal",
    ) -> None:
        """Best-effort incremental index after write.

        filepath may be None for ledger-only entries (projection failed/skipped).
        """
        if not self._indexer:
            return
        try:
            rel_path = (
                str(filepath.relative_to(self._workspace_path)) if filepath else None
            )
            await self._indexer.index_entry_direct(
                content=text, scope_key=scope_key, source_type="daily_note",
                source_path=rel_path, source_date=today,
                entry_id=entry_id, source_session_id=source_session_id,
                principal_id=principal_id, visibility=visibility,
            )
        except Exception:
            logger.warning("memory_index_after_write_failed",
                           path=str(filepath), scope_key=scope_key)

    async def process_flush_candidates(
        self,
        candidates: list[ResolvedFlushCandidate],
        *,
        min_confidence: float = 0.5,
    ) -> int:
        """Filter and persist flush candidates to today's daily note.

        Counting rule (P2-M2d):
        - result.ledger_written or result.projection_written → written += 1
        - LedgerWriteError → break (DB unavailable)
        - MemoryWriteError → break (no-ledger fallback: file limit reached)
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
                result = await self.append_daily_note(
                    text=candidate.candidate_text,
                    scope_key=candidate.scope_key,
                    source="compaction_flush",
                    source_session_id=candidate.source_session_id,
                    principal_id=candidate.principal_id,
                )
                if result.ledger_written or result.projection_written:
                    written += 1
            except (MemoryWriteError, LedgerWriteError):
                logger.warning(
                    "flush_candidate_write_failed",
                    scope_key=candidate.scope_key,
                    source_session_id=candidate.source_session_id,
                )
                break

        logger.info(
            "flush_candidates_processed",
            total=len(candidates),
            written=written,
        )
        return written
