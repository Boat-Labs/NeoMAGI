"""Edit a text/code file via exact string replacement within the workspace.

V1: old_string → new_string, unique match required by default.
replace_all=true for multi-match replacement.
Requires read-before-edit via read state staleness check.
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


class EditFileTool(BaseTool):
    """Edit a file via exact string replacement within the workspace."""

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
        return "edit_file"

    @property
    def description(self) -> str:
        return (
            "Edit a file by replacing an exact string match with new content. "
            "Requires prior read_file. The old_string must match exactly once "
            "unless replace_all=true."
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
                "old_string": {
                    "type": "string",
                    "description": "Exact string to find in the file.",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement string.",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": (
                        "Replace all occurrences. Default: false (unique match required)."
                    ),
                },
            },
            "required": ["file_path", "old_string", "new_string"],
        }

    async def execute(self, arguments: dict, context: ToolContext | None = None) -> dict:
        raw_path = arguments.get("file_path", "")
        old_string = arguments.get("old_string")
        new_string = arguments.get("new_string")
        replace_all = arguments.get("replace_all", False)

        if old_string is None or not isinstance(old_string, str):
            return {"error_code": "INVALID_ARGS", "message": "old_string must be a string."}
        if new_string is None or not isinstance(new_string, str):
            return {"error_code": "INVALID_ARGS", "message": "new_string must be a string."}
        if old_string == new_string:
            return {
                "error_code": "INVALID_ARGS",
                "message": "old_string and new_string are identical.",
            }

        result = validate_workspace_path(raw_path, self._workspace_dir)
        if isinstance(result, dict):
            return result
        target, relative_path = result
        canonical_path = str(target)
        session_id = context.session_id if context else "unknown"

        if not target.is_file():
            return {"error_code": "FILE_NOT_FOUND", "message": f"File not found: {relative_path}"}

        # Read-before-edit check
        stat = target.stat()
        stale_err = self._read_state_store.check_staleness(
            session_id, canonical_path,
            current_mtime_ns=stat.st_mtime_ns, current_size=stat.st_size,
        )
        if stale_err is not None:
            msg = (
                "File has not been read yet. Use read_file first."
                if stale_err == "READ_REQUIRED"
                else "File has been modified since last read. Use read_file to refresh."
            )
            return {"error_code": stale_err, "message": msg}

        # Read current content (newline-safe: binary read + UTF-8 decode)
        try:
            file_content = target.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return {"error_code": "READ_ERROR", "message": f"Failed to read file: {e}"}

        # Count occurrences
        match_count = file_content.count(old_string)

        if match_count == 0:
            return {
                "error_code": "NO_MATCH",
                "message": "old_string not found in file.",
            }

        if match_count > 1 and not replace_all:
            return {
                "error_code": "MULTIPLE_MATCHES",
                "message": (
                    f"old_string found {match_count} times. "
                    "Provide more context for unique match, or set replace_all=true."
                ),
            }

        # Perform replacement
        new_content = file_content.replace(old_string, new_string, -1 if replace_all else 1)

        try:
            target.write_bytes(new_content.encode("utf-8"))
        except OSError as e:
            logger.exception("file_edit_error", path=str(target))
            return {"error_code": "WRITE_ERROR", "message": f"Failed to write file: {e}"}

        replacements_made = match_count if replace_all else 1
        logger.info(
            "file_edited",
            path=relative_path,
            replacements=replacements_made,
            session_id=session_id,
        )

        return {
            "ok": True,
            "file_path": canonical_path,
            "relative_path": relative_path,
            "replacements": replacements_made,
        }
