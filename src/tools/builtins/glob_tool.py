"""Glob file pattern matching within the workspace.

Uses asyncio.to_thread for non-blocking filesystem operations.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from src.tools.base import BaseTool, RiskLevel, ToolGroup, ToolMode

if TYPE_CHECKING:
    from src.tools.context import ToolContext

logger = structlog.get_logger()

_DEFAULT_MAX_RESULTS = 200


class GlobTool(BaseTool):
    """Find files matching a glob pattern within the workspace."""

    def __init__(self, workspace_dir: Path, *, max_results: int = _DEFAULT_MAX_RESULTS) -> None:
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
        return "glob"

    @property
    def description(self) -> str:
        return (
            "Find files matching a glob pattern within the workspace. "
            f"Returns up to {_DEFAULT_MAX_RESULTS} matching file paths."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Glob pattern, e.g. '**/*.py', 'src/**/*.ts', '*.md'. "
                        "Relative to workspace root."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Subdirectory to search in (relative to workspace). "
                        "Default: workspace root."
                    ),
                },
            },
            "required": ["pattern"],
        }

    async def execute(self, arguments: dict, context: ToolContext | None = None) -> dict:
        pattern = arguments.get("pattern", "")
        if not isinstance(pattern, str) or not pattern:
            return {"error_code": "INVALID_ARGS", "message": "pattern must be a non-empty string."}

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
            matches = await asyncio.to_thread(self._glob_sync, search_dir, pattern)
        except Exception as e:
            logger.exception("glob_error", pattern=pattern)
            return {"error_code": "GLOB_ERROR", "message": f"Glob failed: {e}"}

        truncated = len(matches) > self._max_results
        matches = matches[: self._max_results]

        # Convert to workspace-relative paths
        relative_paths = []
        for m in matches:
            resolved = m.resolve()
            if resolved.is_relative_to(self._workspace_dir):
                relative_paths.append(str(resolved.relative_to(self._workspace_dir)))

        return {
            "matches": relative_paths,
            "count": len(relative_paths),
            "truncated": truncated,
            "pattern": pattern,
        }

    def _glob_sync(self, search_dir: Path, pattern: str) -> list[Path]:
        """Synchronous glob, runs in thread pool.

        Iterates lazily, collects at most ``max_results + 1`` entries
        (the extra one detects truncation), then sorts the bounded
        result set.  No full materialization of the glob iterator.
        """
        cap = self._max_results + 1
        filtered: list[Path] = []
        for p in search_dir.glob(pattern):
            resolved = p.resolve()
            if resolved.is_file() and resolved.is_relative_to(self._workspace_dir):
                filtered.append(p)
                if len(filtered) >= cap:
                    break
        filtered.sort()
        return filtered
