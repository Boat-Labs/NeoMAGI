from __future__ import annotations

from pathlib import Path

from src.tools.builtins.current_time import CurrentTimeTool
from src.tools.builtins.memory_search import MemorySearchTool
from src.tools.builtins.read_file import ReadFileTool
from src.tools.registry import ToolRegistry


def register_builtins(registry: ToolRegistry, workspace_dir: Path) -> None:
    """Register all built-in tools with the registry."""
    registry.register(CurrentTimeTool())
    registry.register(MemorySearchTool())
    registry.register(ReadFileTool(workspace_dir))
