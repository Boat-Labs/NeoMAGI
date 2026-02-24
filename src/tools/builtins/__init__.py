from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from src.tools.builtins.current_time import CurrentTimeTool
from src.tools.builtins.memory_append import MemoryAppendTool
from src.tools.builtins.memory_search import MemorySearchTool
from src.tools.builtins.read_file import ReadFileTool
from src.tools.builtins.soul_propose import SoulProposeTool
from src.tools.builtins.soul_rollback import SoulRollbackTool
from src.tools.builtins.soul_status import SoulStatusTool
from src.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from src.memory.evolution import EvolutionEngine
    from src.memory.searcher import MemorySearcher
    from src.memory.writer import MemoryWriter


def register_builtins(
    registry: ToolRegistry,
    workspace_dir: Path,
    *,
    memory_searcher: MemorySearcher | None = None,
    memory_writer: MemoryWriter | None = None,
    evolution_engine: EvolutionEngine | None = None,
) -> None:
    """Register all built-in tools with the registry.

    MemorySearchTool is always registered (graceful degradation when searcher is None).
    Tools requiring non-None dependencies are registered only when deps are available.
    """
    registry.register(CurrentTimeTool())
    registry.register(MemorySearchTool(memory_searcher))
    registry.register(ReadFileTool(workspace_dir))

    if memory_writer is not None:
        registry.register(MemoryAppendTool(memory_writer))

    if evolution_engine is not None:
        registry.register(SoulProposeTool(evolution_engine))
        registry.register(SoulStatusTool(evolution_engine))
        registry.register(SoulRollbackTool(evolution_engine))
