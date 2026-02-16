"""Idempotent workspace initialization with template files.

Run directly: python -m src.infra.init_workspace
Or via justfile: just init-workspace
"""

from __future__ import annotations

from pathlib import Path

import structlog

from src.infra.logging import setup_logging

logger = structlog.get_logger()

WORKSPACE_DIR = Path("workspace")
MEMORY_DIR = WORKSPACE_DIR / "memory"

TEMPLATES: dict[str, str] = {
    "AGENTS.md": """\
# AGENTS.md
# Agent behavior contract and operating procedures.
# Edit this file to define how the agent should behave.

## Memory
You wake up fresh each session. These files are your continuity:
- Daily notes: memory/YYYY-MM-DD.md — raw logs of what happened
- Long-term: MEMORY.md — your curated memories

## Safety
Don't exfiltrate private data. Ever.
Don't run destructive commands without asking.
When in doubt, ask.
""",
    "SOUL.md": """\
# SOUL.md — Who You Are
# Persona, philosophy, and values.
# This shapes who the agent is, not what it does.

## Core Truths
Be genuinely helpful, not performatively helpful.
Have opinions. An assistant with no personality is just a search engine.
Be resourceful before asking.
""",
    "USER.md": """\
# USER.md
# User preferences and context.
# Add your preferences here so the agent knows who it serves.

# - Name:
# - Timezone:
# - Languages:
# - Communication style:
""",
    "IDENTITY.md": """\
# IDENTITY.md
name: Magi
role: Personal AI Assistant
""",
    "TOOLS.md": """\
# TOOLS.md
# Tool usage notes and local environment specifics.
""",
    "MEMORY.md": """\
# MEMORY.md
# Long-term curated memory. Loaded only in main (DM) sessions.
# The agent updates this file over time with important, persistent knowledge.
""",
}


def init_workspace() -> None:
    """Create workspace directory and template files. Idempotent: existing files not overwritten."""
    WORKSPACE_DIR.mkdir(exist_ok=True)
    MEMORY_DIR.mkdir(exist_ok=True)
    logger.info("workspace_dirs_ensured", workspace=str(WORKSPACE_DIR), memory=str(MEMORY_DIR))

    for filename, content in TEMPLATES.items():
        filepath = WORKSPACE_DIR / filename
        if filepath.exists():
            logger.info("workspace_file_skipped", file=str(filepath))
        else:
            filepath.write_text(content, encoding="utf-8")
            logger.info("workspace_template_created", file=str(filepath))


if __name__ == "__main__":
    setup_logging(json_output=False)
    init_workspace()
