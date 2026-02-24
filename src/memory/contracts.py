"""Memory-side shared contract types.

Memory layer owns these DTOs. Zero dependency on src.agent.*.
AgentLoop maps from agent-layer types at the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedFlushCandidate:
    """Memory-side DTO for scope-resolved flush candidates.

    Constructed by AgentLoop (mapping from agent-layer MemoryFlushCandidate).
    Consumed by MemoryWriter.process_flush_candidates().

    Memory layer does NOT import or depend on src.agent.* types.
    AgentLoop performs the one-time mapping at the boundary:
        MemoryFlushCandidate → ResolvedFlushCandidate
    """

    candidate_text: str
    scope_key: str
    source_session_id: str
    confidence: float = 0.0
    constraint_tags: tuple[str, ...] = ()
