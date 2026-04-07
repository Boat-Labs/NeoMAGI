"""Grep text/regex search within workspace files.

Uses asyncio.to_thread for non-blocking filesystem operations.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from src.tools.base import BaseTool, RiskLevel, ToolGroup, ToolMode

if TYPE_CHECKING:
    from src.tools.context import ToolContext

logger = structlog.get_logger()

_DEFAULT_MAX_RESULTS = 100
_MAX_FILE_SIZE_BYTES = 2 * 1024 * 1024  # 2 MB per file
_MAX_LINE_LENGTH = 500  # truncate long matching lines


class GrepTool(BaseTool):
    """Search for text or regex patterns within workspace files."""

    def __init__(
        self,
        workspace_dir: Path,
        *,
        max_results: int = _DEFAULT_MAX_RESULTS,
    ) -> None:
        self._workspace_dir = workspace_dir.resolve()
        self._max_results = max_results

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
    def is_concurrency_safe(self) -> bool:
        return True

    @property
    def name(self) -> str:
        return "grep"

    @property
    def description(self) -> str:
        return (
            "Search for text or regex patterns in workspace files. "
            f"Returns up to {_DEFAULT_MAX_RESULTS} matching lines with file paths and line numbers."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Search pattern (regex supported).",
                },
                "glob": {
                    "type": "string",
                    "description": (
                        "File glob pattern to filter files, e.g. '**/*.py'. "
                        "Default: '**/*'."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Subdirectory to search in (relative to workspace). "
                        "Default: workspace root."
                    ),
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive search. Default: false.",
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, arguments: dict, context: ToolContext | None = None) -> dict:
        pattern_str = arguments.get("pattern", "")
        if not isinstance(pattern_str, str) or not pattern_str:
            return {"error_code": "INVALID_ARGS", "message": "pattern must be a non-empty string."}

        flags = 0
        if arguments.get("case_insensitive", False):
            flags |= re.IGNORECASE

        try:
            regex = re.compile(pattern_str, flags)
        except re.error as e:
            return {"error_code": "INVALID_PATTERN", "message": f"Invalid regex: {e}"}

        file_glob = arguments.get("glob", "**/*")
        if not isinstance(file_glob, str) or not file_glob:
            file_glob = "**/*"

        sub_path = arguments.get("path", "")
        if sub_path:
            search_dir = (self._workspace_dir / sub_path).resolve()
            if not search_dir.is_relative_to(self._workspace_dir):
                return {
                    "error_code": "ACCESS_DENIED",
                    "message": "Path escapes workspace boundary.",
                }
        else:
            search_dir = self._workspace_dir

        if not search_dir.is_dir():
            return {"error_code": "INVALID_ARGS", "message": f"Directory not found: {sub_path}"}

        try:
            results = await asyncio.to_thread(
                self._grep_sync, search_dir, regex, file_glob
            )
        except Exception as e:
            logger.exception("grep_error", pattern=pattern_str)
            return {"error_code": "GREP_ERROR", "message": f"Grep failed: {e}"}

        truncated = len(results) > self._max_results
        results = results[: self._max_results]

        return {
            "matches": results,
            "count": len(results),
            "truncated": truncated,
            "pattern": pattern_str,
        }

    def _grep_sync(
        self, search_dir: Path, regex: re.Pattern, file_glob: str
    ) -> list[dict]:
        """Synchronous grep, runs in thread pool.

        Iterates the glob lazily and collects at most ``max_results + 1``
        matching lines.  File enumeration order is filesystem-dependent;
        no full sort of the glob iterator.
        """
        cap = self._max_results + 1
        results: list[dict] = []

        for file_path in search_dir.glob(file_glob):
            if len(results) >= cap:
                break
            resolved = file_path.resolve()
            if not resolved.is_file():
                continue
            if not resolved.is_relative_to(self._workspace_dir):
                continue
            if resolved.stat().st_size > _MAX_FILE_SIZE_BYTES:
                continue

            try:
                text = resolved.read_bytes().decode("utf-8")
            except (OSError, UnicodeDecodeError):
                continue

            rel_path = str(resolved.relative_to(self._workspace_dir))
            for line_no, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    display_line = line[:_MAX_LINE_LENGTH]
                    if len(line) > _MAX_LINE_LENGTH:
                        display_line += "..."
                    results.append({
                        "file": rel_path,
                        "line": line_no,
                        "content": display_line,
                    })
                    if len(results) >= cap:
                        break

        return results
