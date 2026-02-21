from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from src.tools.base import ToolMode

if TYPE_CHECKING:
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
    ) -> None:
        self._workspace_dir = workspace_dir
        self._tool_registry = tool_registry

    def build(self, session_id: str, mode: ToolMode) -> str:
        """Build the complete system prompt by concatenating all non-empty layers."""
        layers = [
            self._layer_identity(),
            self._layer_tooling(mode),
            self._layer_safety(mode),
            self._layer_skills(),
            self._layer_workspace(session_id),
            self._layer_memory_recall(),
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

    def _layer_workspace(self, session_id: str) -> str:
        """Load workspace bootstrap files and concatenate their contents."""
        parts: list[str] = []

        for filename in WORKSPACE_CONTEXT_FILES:
            content = self._read_workspace_file(filename)
            if content:
                parts.append(content)
                logger.info("prompt_file_injected", file=filename, layer="workspace")

        # MEMORY.md only for main session
        if session_id == "main":
            for filename in MAIN_SESSION_ONLY:
                content = self._read_workspace_file(filename)
                if content:
                    parts.append(content)
                    logger.info("prompt_file_injected", file=filename, layer="workspace")

        if not parts:
            return ""

        return "## Project Context\n\n" + "\n\n---\n\n".join(parts)

    def _layer_memory_recall(self) -> str:
        # Placeholder — no memory search in M1.2
        return ""

    def _layer_datetime(self) -> str:
        now = datetime.now(UTC)
        return f"Current date and time (UTC): {now.strftime('%Y-%m-%d %H:%M:%S')}"

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
