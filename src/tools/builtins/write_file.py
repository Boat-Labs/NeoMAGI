"""Write/create a text/code file within the workspace.

V1: UTF-8 only, newline-safe I/O.
Default create-only; explicit overwrite=true for replace.
Replace requires read-before-write via read state staleness check.
Full-file update requires complete (offset=0, truncated=false) read state.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from src.tools.base import BaseTool, RiskLevel, ToolGroup, ToolMode
from src.tools.read_state import (
    ReadStateStore,
    get_read_state_store,
    validate_workspace_path,
)

if TYPE_CHECKING:
    from src.tools.context import ToolContext

logger = structlog.get_logger()


def _coerce_bool(value: object) -> bool:
    """Strict boolean coercion for tool arguments.

    Only Python ``True`` or the string ``"true"`` (case-insensitive) yield
    ``True``.  Everything else — including truthy strings like ``"false"``
    — yields ``False``.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() == "true"
    return False


class WriteFileTool(BaseTool):
    """Write or create a text/code file within the workspace."""

    def __init__(
        self,
        workspace_dir: Path,
        *,
        read_state_store: ReadStateStore | None = None,
    ) -> None:
        self._workspace_dir = workspace_dir.resolve()
        self._read_state_store = read_state_store or get_read_state_store()

    @property
    def group(self) -> ToolGroup:
        return ToolGroup.code

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.coding})

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.high

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return (
            "Create a new file or overwrite an existing file within the workspace. "
            "Default: create-only. Set overwrite=true to replace an existing file "
            "(requires prior read_file)."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or relative path within workspace.",
                },
                "content": {
                    "type": "string",
                    "description": "Content to write to the file.",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": "If true, allow replacing an existing file. Default: false.",
                },
            },
            "required": ["file_path", "content"],
        }

    async def execute(self, arguments: dict, context: ToolContext | None = None) -> dict:
        raw_path = arguments.get("file_path", "")
        content = arguments.get("content")
        overwrite = _coerce_bool(arguments.get("overwrite", False))

        if content is None:
            return {"error_code": "INVALID_ARGS", "message": "content is required."}
        if not isinstance(content, str):
            return {"error_code": "INVALID_ARGS", "message": "content must be a string."}

        result = validate_workspace_path(raw_path, self._workspace_dir)
        if isinstance(result, dict):
            return result
        target, relative_path = result
        canonical_path = str(target)
        session_id = context.session_id if context else "unknown"

        file_exists = target.is_file()

        if file_exists and not overwrite:
            return {
                "error_code": "FILE_EXISTS",
                "message": (
                    f"File already exists: {relative_path}. "
                    "Set overwrite=true to replace."
                ),
            }

        if file_exists and overwrite:
            # Read-before-write check
            stat = target.stat()
            stale_err = self._read_state_store.check_staleness(
                session_id, canonical_path,
                current_mtime_ns=stat.st_mtime_ns, current_size=stat.st_size,
            )
            if stale_err is not None:
                msg = (
                    "File has not been read or has been modified since last read. "
                    "Use read_file first."
                    if stale_err == "READ_REQUIRED"
                    else "File has been modified since last read. Use read_file to refresh."
                )
                return {"error_code": stale_err, "message": msg}

            # Full-file update requires complete read
            if not self._read_state_store.is_full_read(session_id, canonical_path):
                return {
                    "error_code": "PARTIAL_READ",
                    "message": (
                        "Cannot overwrite file with only partial read state. "
                        "Read the complete file (offset=0, no truncation) first."
                    ),
                }

        # Ensure parent directory exists (for new files in subdirs)
        target.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Newline-safe write: binary mode + explicit UTF-8 encode
            target.write_bytes(content.encode("utf-8"))
        except OSError as e:
            logger.exception("file_write_error", path=str(target))
            return {"error_code": "WRITE_ERROR", "message": f"Failed to write file: {e}"}

        operation = "update" if file_exists else "create"
        logger.info(
            "file_written",
            path=relative_path,
            operation=operation,
            size=len(content),
            session_id=session_id,
        )

        return {
            "ok": True,
            "operation": operation,
            "file_path": canonical_path,
            "relative_path": relative_path,
            "size": len(content.encode("utf-8")),
        }
