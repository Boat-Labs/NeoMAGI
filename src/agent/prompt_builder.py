from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# Workspace bootstrap files loaded every turn
ALWAYS_LOAD = ["AGENTS.md", "SOUL.md", "USER.md", "IDENTITY.md", "TOOLS.md"]
# Conditional files
MAIN_SESSION_ONLY = ["MEMORY.md"]


class PromptBuilder:
    """Assembles the system prompt from 7 layers.

    Layers:
    1. Base identity (hardcoded minimal identity declaration)
    2. Tooling (placeholder for tool descriptions)
    3. Safety (placeholder for safety guardrails)
    4. Skills (placeholder for available skills)
    5. Workspace context (loaded from workspace/ directory)
    6. Memory recall (placeholder for memory_search results)
    7. Date/Time + timezone
    """

    def __init__(self, workspace_dir: Path) -> None:
        self._workspace_dir = workspace_dir

    def build(self, session_id: str = "main") -> str:
        """Build the complete system prompt by concatenating all non-empty layers."""
        layers = [
            self._layer_identity(),
            self._layer_tooling(),
            self._layer_safety(),
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

    def _layer_tooling(self) -> str:
        # Placeholder — no tools available in M1.1
        return ""

    def _layer_safety(self) -> str:
        # Placeholder — will be expanded in later milestones
        return ""

    def _layer_skills(self) -> str:
        # Placeholder — no skills in M1.1
        return ""

    def _layer_workspace(self, session_id: str) -> str:
        """Load workspace bootstrap files and concatenate their contents."""
        parts: list[str] = []

        for filename in ALWAYS_LOAD:
            content = self._read_workspace_file(filename)
            if content:
                parts.append(content)

        # MEMORY.md only for main session
        if session_id == "main":
            for filename in MAIN_SESSION_ONLY:
                content = self._read_workspace_file(filename)
                if content:
                    parts.append(content)

        if not parts:
            return ""

        return "## Project Context\n\n" + "\n\n---\n\n".join(parts)

    def _layer_memory_recall(self) -> str:
        # Placeholder — no memory search in M1.1
        return ""

    def _layer_datetime(self) -> str:
        now = datetime.now(UTC)
        return f"Current date and time (UTC): {now.strftime('%Y-%m-%d %H:%M:%S')}"

    def _read_workspace_file(self, filename: str) -> str:
        """Read a file from workspace. Returns empty string if not found."""
        filepath = self._workspace_dir / filename
        if not filepath.is_file():
            logger.debug("Workspace file not found, skipping: %s", filepath)
            return ""
        try:
            content = filepath.read_text(encoding="utf-8").strip()
            logger.debug("Loaded workspace file: %s (%d chars)", filepath, len(content))
            return content
        except OSError:
            logger.exception("Failed to read workspace file: %s", filepath)
            return ""
