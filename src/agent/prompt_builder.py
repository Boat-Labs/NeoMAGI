from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from src.tools.base import ToolMode

if TYPE_CHECKING:
    from src.config.settings import MemorySettings
    from src.tools.registry import ToolRegistry

logger = structlog.get_logger()

# Workspace context files loaded every turn (priority order)
WORKSPACE_CONTEXT_FILES = ["AGENTS.md", "USER.md", "SOUL.md", "IDENTITY.md"]
# Conditional files
MAIN_SESSION_ONLY = ["MEMORY.md"]


class PromptBuilder:
    """Assembles the system prompt from 7 layers.

    Layers:
    1. Base identity (hardcoded minimal identity declaration)
    2. Tooling (tool descriptions from registry + TOOLS.md)
    3. Safety (placeholder for safety guardrails)
    4. Skills (placeholder for available skills)
    5. Workspace context (AGENTS/SOUL/USER/IDENTITY from workspace/)
    6. Memory recall (placeholder for memory_search results)
    7. Date/Time + timezone
    """

    def __init__(
        self,
        workspace_dir: Path,
        tool_registry: ToolRegistry | None = None,
        memory_settings: MemorySettings | None = None,
    ) -> None:
        self._workspace_dir = workspace_dir
        self._tool_registry = tool_registry
        self._memory_settings = memory_settings

    def build(
        self,
        session_id: str,
        mode: ToolMode,
        compacted_context: str | None = None,
        *,
        scope_key: str = "main",
        recent_messages: list[str] | None = None,
    ) -> str:
        """Build the complete system prompt by concatenating all non-empty layers.

        When compacted_context is provided (after compaction), it is injected
        between workspace context and memory recall as a [会话摘要] block.

        scope_key: from session_resolver (ADR 0034), consumed by workspace
                   and memory_recall layers.
        recent_messages: Phase 3, for memory recall keyword extraction.
        """
        layers = [
            self._layer_identity(),
            self._layer_tooling(mode),
            self._layer_safety(mode),
            self._layer_skills(),
            self._layer_workspace(session_id, scope_key=scope_key),
            self._layer_compacted_context(compacted_context),
            self._layer_memory_recall(
                scope_key=scope_key, recent_messages=recent_messages
            ),
            self._layer_datetime(),
        ]
        return "\n\n".join(layer for layer in layers if layer)

    def _layer_identity(self) -> str:
        return (
            "You are Magi, a personal AI assistant. "
            "You have persistent memory and act in the user's information interests. "
            "Be helpful, concise, and honest."
        )

    def _layer_tooling(self, mode: ToolMode) -> str:
        """Generate tooling layer from ToolRegistry + TOOLS.md."""
        parts: list[str] = []

        # Tool descriptions from registry (mode-filtered)
        if self._tool_registry:
            tools = self._tool_registry.list_tools(mode)
            if tools:
                lines = ["## Available Tools", ""]
                for tool in tools:
                    lines.append(f"- **{tool.name}**: {tool.description}")
                parts.append("\n".join(lines))
                logger.debug("tooling_layer_injected", tool_count=len(tools))

        # TOOLS.md content (moved from workspace context layer)
        tools_md = self._read_workspace_file("TOOLS.md")
        if tools_md:
            parts.append(tools_md)
            logger.info("prompt_file_injected", file="TOOLS.md", layer="tooling")

        return "\n\n".join(parts) if parts else ""

    def _layer_safety(self, mode: ToolMode) -> str:
        """Generate safety layer. Includes mode-specific constraints."""
        if mode == ToolMode.chat_safe:
            return (
                "## Safety\n\n"
                "Current session mode: **chat_safe**.\n"
                "Only conversational tools (memory search, current time, etc.) are available.\n"
                "Code-editing and file-system tools are disabled in this mode.\n\n"
                "If the user requests code operations, explain that these tools are not "
                "available in the current mode and will be enabled in a future version."
            )
        return ""

    def _layer_skills(self) -> str:
        # Placeholder — no skills in M1.2
        return ""

    def _layer_workspace(
        self, session_id: str, *, scope_key: str = "main"
    ) -> str:
        """Load workspace bootstrap files and concatenate their contents."""
        parts: list[str] = []

        for filename in WORKSPACE_CONTEXT_FILES:
            content = self._read_workspace_file(filename)
            if content:
                parts.append(content)
                logger.info("prompt_file_injected", file=filename, layer="workspace")

        # MEMORY.md only for main scope
        if scope_key == "main":
            for filename in MAIN_SESSION_ONLY:
                content = self._read_workspace_file(filename)
                if content:
                    parts.append(content)
                    logger.info("prompt_file_injected", file=filename, layer="workspace")

        # Daily notes auto-load (Phase 1)
        daily_notes = self._load_daily_notes(scope_key=scope_key)
        if daily_notes:
            parts.append(daily_notes)

        if not parts:
            return ""

        return "## Project Context\n\n" + "\n\n---\n\n".join(parts)

    def _layer_compacted_context(self, compacted_context: str | None) -> str:
        """Inject rolling summary from compaction (if any)."""
        if not compacted_context:
            return ""
        return f"## 会话摘要\n\n{compacted_context}"

    def _layer_memory_recall(
        self,
        *,
        scope_key: str = "main",
        recent_messages: list[str] | None = None,
    ) -> str:
        # Placeholder — Phase 3 implementation
        return ""

    def _layer_datetime(self) -> str:
        now = datetime.now(UTC)
        return f"Current date and time (UTC): {now.strftime('%Y-%m-%d %H:%M:%S')}"

    def _load_daily_notes(self, *, scope_key: str = "main") -> str:
        """Load today + yesterday daily notes, filtered by scope_key.

        scope_key comes from session_resolver (ADR 0034).
        Only entries matching the current scope_key are included.

        Old data compatibility: entries without scope metadata are treated as
        scope_key='main' (all pre-M3 daily notes were written in main session).

        Format injected:
        [Recent Daily Notes]
        === 2026-02-22 ===
        {content of memory/2026-02-22.md, filtered by scope}
        """
        load_days = 2
        max_tokens = 4000
        if self._memory_settings:
            load_days = self._memory_settings.daily_notes_load_days
            max_tokens = self._memory_settings.daily_notes_max_tokens

        memory_dir = self._workspace_dir / "memory"
        if not memory_dir.is_dir():
            return ""

        today = date.today()
        parts: list[str] = []

        for offset in range(load_days):
            target_date = today - timedelta(days=offset)
            filepath = memory_dir / f"{target_date.isoformat()}.md"
            if not filepath.is_file():
                continue
            try:
                raw = filepath.read_text(encoding="utf-8").strip()
                if not raw:
                    continue
                filtered = self._filter_entries_by_scope(raw, scope_key)
                if not filtered:
                    continue
                # Rough truncation: ~4 chars per token estimate
                max_chars = max_tokens * 4
                if len(filtered) > max_chars:
                    filtered = filtered[:max_chars] + "\n...(truncated)"
                parts.append(f"=== {target_date.isoformat()} ===\n{filtered}")
                logger.info(
                    "daily_notes_loaded",
                    date=target_date.isoformat(),
                    scope_key=scope_key,
                    chars=len(filtered),
                )
            except OSError:
                logger.exception("daily_notes_read_error", path=str(filepath))

        if not parts:
            return ""

        return "[Recent Daily Notes]\n" + "\n\n".join(parts)

    @staticmethod
    def _filter_entries_by_scope(content: str, scope_key: str) -> str:
        """Filter daily note entries by scope_key.

        Each entry starts with '---'. Entries with 'scope: X' metadata
        are filtered. Entries without scope metadata are treated as
        scope_key='main' (old data compatibility).
        """
        # Split by entry separator
        entries = re.split(r"^---$", content, flags=re.MULTILINE)
        filtered: list[str] = []

        for entry in entries:
            stripped = entry.strip()
            if not stripped:
                continue

            # Check if entry has scope metadata
            scope_match = re.search(r"scope:\s*(\S+)", stripped)
            if scope_match:
                entry_scope = scope_match.group(1).rstrip(")")
                if entry_scope != scope_key:
                    continue
            else:
                # No scope metadata → treat as 'main' (old data compatibility)
                if scope_key != "main":
                    continue

            filtered.append(stripped)

        return "\n\n".join(filtered)

    def _read_workspace_file(self, filename: str) -> str:
        """Read a file from workspace. Returns empty string if not found."""
        filepath = self._workspace_dir / filename
        if not filepath.is_file():
            logger.debug("workspace_file_skipped", path=str(filepath))
            return ""
        try:
            content = filepath.read_text(encoding="utf-8").strip()
            logger.debug("workspace_file_loaded", path=str(filepath), chars=len(content))
            return content
        except OSError:
            logger.exception("workspace_file_read_error", path=str(filepath))
            return ""
