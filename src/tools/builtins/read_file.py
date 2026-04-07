"""Read a text/code file from the workspace with line range support and read state tracking.

V1: UTF-8 only, newline-safe I/O (no implicit CRLF→LF conversion).
Accepts both `file_path` (absolute, preferred) and `path` (relative, legacy alias).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from src.tools.base import BaseTool, RiskLevel, ToolGroup, ToolMode
from src.tools.read_state import (
    ReadScope,
    ReadState,
    ReadStateStore,
    get_read_state_store,
    validate_workspace_path,
)

if TYPE_CHECKING:
    from src.tools.context import ToolContext

logger = structlog.get_logger()

# Maximum lines returned in a single read (output truncation).
_DEFAULT_MAX_LINES = 2000


def _read_file_bytes(target: Path, relative_path: str) -> dict | bytes:
    """Read file as bytes. Returns error dict on failure, raw bytes on success."""
    try:
        return target.read_bytes()
    except OSError as e:
        logger.exception("file_read_error", path=str(target))
        return {"error_code": "READ_ERROR", "message": f"Failed to read file: {e}"}


def _decode_utf8(raw_bytes: bytes, relative_path: str) -> dict | str:
    """Decode bytes as UTF-8. Returns error dict on failure."""
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "error_code": "ENCODING_ERROR",
            "message": f"File is not valid UTF-8: {relative_path}",
        }


def _resolve_offset_limit(arguments: dict, max_lines: int) -> tuple[int, int]:
    """Extract and clamp offset/limit from arguments."""
    offset = max(0, arguments.get("offset", 0) or 0)
    limit = arguments.get("limit")
    if limit is not None:
        limit = min(max(1, limit), max_lines)
    else:
        limit = max_lines
    return offset, limit


def _format_numbered_lines(lines: list[str], offset: int) -> str:
    """Format lines with 1-based line numbers and tab separator."""
    content_lines = []
    for i, line in enumerate(lines):
        line_no = offset + i + 1
        content_lines.append(f"{line_no}\t{line.rstrip(chr(10)).rstrip(chr(13))}")
    return "\n".join(content_lines)


class ReadFileTool(BaseTool):
    """Read a text/code file from the workspace directory with path safety enforcement."""

    def __init__(
        self,
        workspace_dir: Path,
        *,
        read_state_store: ReadStateStore | None = None,
        max_lines: int = _DEFAULT_MAX_LINES,
    ) -> None:
        self._workspace_dir = workspace_dir.resolve()
        self._read_state_store = read_state_store or get_read_state_store()
        self._max_lines = max_lines

    @property
    def group(self) -> ToolGroup:
        return ToolGroup.code

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.coding})

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.low

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read a text/code file from the workspace. "
            "Supports line range via offset/limit. Returns content with line numbers."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": (
                        "Absolute path within the workspace, or relative path. "
                        "Preferred parameter name."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Legacy alias for file_path. Relative path within workspace."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": "Starting line number (0-based). Default: 0.",
                    "minimum": 0,
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        f"Max lines to return. Default/max: {_DEFAULT_MAX_LINES}."
                    ),
                    "minimum": 1,
                },
            },
            "required": [],
        }

    async def execute(self, arguments: dict, context: ToolContext | None = None) -> dict:
        raw_path = arguments.get("file_path") or arguments.get("path", "")
        result = validate_workspace_path(raw_path, self._workspace_dir)
        if isinstance(result, dict):
            return result
        target, relative_path = result

        if not target.is_file():
            return {"error_code": "FILE_NOT_FOUND", "message": f"File not found: {relative_path}"}

        raw_bytes = _read_file_bytes(target, relative_path)
        if isinstance(raw_bytes, dict):
            return raw_bytes

        text = _decode_utf8(raw_bytes, relative_path)
        if isinstance(text, dict):
            return text

        all_lines = text.splitlines(keepends=True)
        offset, limit = _resolve_offset_limit(arguments, self._max_lines)
        sliced = all_lines[offset : offset + limit]
        truncated = (offset + limit) < len(all_lines)

        stat = target.stat()
        self._record_read_state(
            target, relative_path, stat, offset, limit, truncated, context,
        )

        return {
            "content": _format_numbered_lines(sliced, offset),
            "file_path": str(target),
            "relative_path": relative_path,
            "total_lines": len(all_lines),
            "offset": offset,
            "limit": limit,
            "lines_returned": len(sliced),
            "truncated": truncated,
            "size": stat.st_size,
        }

    def _record_read_state(
        self,
        target: Path,
        relative_path: str,
        stat: object,
        offset: int,
        limit: int,
        truncated: bool,
        context: ToolContext | None,
    ) -> None:
        session_id = context.session_id if context else "unknown"
        self._read_state_store.record(
            ReadState(
                session_id=session_id,
                file_path=str(target),
                relative_path=relative_path,
                mtime_ns=stat.st_mtime_ns,
                size=stat.st_size,
                read_scope=ReadScope(offset=offset, limit=limit),
                truncated=truncated,
                read_at=datetime.now(UTC),
            )
        )
