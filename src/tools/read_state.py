"""Process-local read state tracking for file transaction contract.

V1: in-memory map keyed by (session_id, file_path). No DB persistence.
Process restart clears all read state; write/edit returns READ_REQUIRED.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class ReadScope:
    """Scope of a read operation."""

    offset: int
    limit: int | None  # None means "read to end"


@dataclass(frozen=True)
class ReadState:
    """Snapshot of a file's state at the time of read."""

    session_id: str
    file_path: str  # canonical absolute path
    relative_path: str
    mtime_ns: int
    size: int
    read_scope: ReadScope
    truncated: bool
    read_at: datetime


class ReadStateStore:
    """Process-local read state tracking.

    V1: single-entry per (session_id, file_path). Last read wins.
    """

    def __init__(self) -> None:
        self._states: dict[tuple[str, str], ReadState] = {}

    def record(self, state: ReadState) -> None:
        """Record a read state. Overwrites any previous state for the same key."""
        key = (state.session_id, state.file_path)
        self._states[key] = state

    def get(self, session_id: str, file_path: str) -> ReadState | None:
        """Get the last read state for a (session_id, file_path) pair."""
        return self._states.get((session_id, file_path))

    def check_staleness(
        self, session_id: str, file_path: str, *, current_mtime_ns: int, current_size: int
    ) -> str | None:
        """Check if the read state is stale.

        Returns None if fresh, or an error code string if stale/missing.
        """
        state = self.get(session_id, file_path)
        if state is None:
            return "READ_REQUIRED"
        if state.mtime_ns != current_mtime_ns or state.size != current_size:
            return "STALE_READ"
        return None

    def is_full_read(self, session_id: str, file_path: str) -> bool:
        """Check if the last read state covers the complete file (offset=0, truncated=False)."""
        state = self.get(session_id, file_path)
        if state is None:
            return False
        return state.read_scope.offset == 0 and not state.truncated

    def clear(self, session_id: str | None = None) -> None:
        """Clear read states. If session_id given, clear only that session."""
        if session_id is None:
            self._states.clear()
        else:
            to_remove = [k for k in self._states if k[0] == session_id]
            for k in to_remove:
                del self._states[k]


# Module-level singleton for process-local read state.
_global_read_state_store = ReadStateStore()


def get_read_state_store() -> ReadStateStore:
    """Get the process-local read state store singleton."""
    return _global_read_state_store


def validate_workspace_path(
    raw_path: str,
    workspace_dir: Path,
) -> tuple[Path, str] | dict:
    """Validate and resolve a file path within the workspace boundary.

    Accepts absolute paths within workspace or relative paths.
    Returns (resolved_path, relative_path_str) on success, or error dict on failure.
    """
    if not isinstance(raw_path, str) or not raw_path:
        return {"error_code": "INVALID_ARGS", "message": "file_path must be a non-empty string."}

    raw = Path(raw_path)

    if raw.is_absolute():
        target = raw.resolve()
    else:
        target = (workspace_dir / raw).resolve()

    # Workspace boundary check (blocks symlink escape and ..)
    if not target.is_relative_to(workspace_dir):
        logger.warning("path_escape_blocked", raw_path=raw_path, resolved=str(target))
        return {"error_code": "ACCESS_DENIED", "message": "Path escapes workspace boundary."}

    try:
        relative_path = str(target.relative_to(workspace_dir))
    except ValueError:
        return {"error_code": "ACCESS_DENIED", "message": "Path escapes workspace boundary."}

    return target, relative_path
