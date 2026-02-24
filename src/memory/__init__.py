"""Memory module: persistent memory write, indexing, retrieval, and evolution."""

from src.memory.contracts import ResolvedFlushCandidate
from src.memory.curator import CurationResult, MemoryCurator
from src.memory.evolution import EvolutionEngine, SoulProposal, SoulVersion
from src.memory.indexer import MemoryIndexer
from src.memory.models import MemoryEntry, SoulVersionRecord
from src.memory.searcher import MemorySearcher, MemorySearchResult
from src.memory.writer import MemoryWriter

__all__ = [
    "CurationResult",
    "EvolutionEngine",
    "MemoryCurator",
    "MemoryEntry",
    "MemoryIndexer",
    "MemorySearchResult",
    "MemorySearcher",
    "MemoryWriter",
    "ResolvedFlushCandidate",
    "SoulProposal",
    "SoulVersion",
    "SoulVersionRecord",
]
