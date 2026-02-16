from __future__ import annotations

import logging
from pathlib import Path

from src.tools.base import BaseTool

logger = logging.getLogger(__name__)


class ReadFileTool(BaseTool):
    """Read a file from the workspace directory with path safety enforcement."""

    def __init__(self, workspace_dir: Path) -> None:
        self._workspace_dir = workspace_dir.resolve()

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read the contents of a file within the workspace directory."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Relative path within workspace, "
                        "e.g. 'IDENTITY.md' or 'memory/2026-02-16.md'."
                    ),
                },
            },
            "required": ["path"],
        }

    async def execute(self, arguments: dict) -> dict:
        raw_path = arguments.get("path", "")

        # Reject absolute paths
        if raw_path.startswith("/"):
            return {
                "error_code": "ACCESS_DENIED",
                "message": "Absolute paths are not allowed. Use a relative path within workspace.",
            }

        # Build target path and resolve (follows symlinks)
        target = (self._workspace_dir / raw_path).resolve()

        # Check that resolved path is still within workspace (prevents ".." and symlink escape)
        if not str(target).startswith(str(self._workspace_dir)):
            logger.warning("Path escape attempt blocked: %s → %s", raw_path, target)
            return {
                "error_code": "ACCESS_DENIED",
                "message": "Path escapes workspace boundary.",
            }

        if not target.is_file():
            return {
                "error_code": "FILE_NOT_FOUND",
                "message": f"File not found: {raw_path}",
            }

        try:
            content = target.read_text(encoding="utf-8")
            return {
                "content": content,
                "path": raw_path,
                "size": len(content),
            }
        except OSError as e:
            logger.exception("Failed to read file: %s", target)
            return {
                "error_code": "READ_ERROR",
                "message": f"Failed to read file: {e}",
            }
