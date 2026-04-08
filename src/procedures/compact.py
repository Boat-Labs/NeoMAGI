"""Purposeful compact — task state extraction for procedure-aware compaction (P2-M2b Slice G, D5).

Extracts structured task state from ActiveProcedure.context using conventional key names.
"""

from __future__ import annotations

import structlog

from src.procedures.handoff import TaskStateSnapshot
from src.procedures.types import ActiveProcedure, ProcedureSpec

logger = structlog.get_logger(__name__)

# Conventional context key names for task state
_OBJECTIVES_KEY = "_objectives"
_TODOS_KEY = "_todos"
_BLOCKERS_KEY = "_blockers"
_LAST_RESULT_KEY = "_last_result"
_PENDING_KEY = "_pending"


def extract_task_state(
    active: ActiveProcedure,
    spec: ProcedureSpec | None = None,
) -> TaskStateSnapshot:
    """Extract structured task state from procedure context.

    Uses conventional ``_``-prefixed keys. Missing keys produce empty defaults.
    """
    ctx = active.context

    def _to_str_tuple(key: str) -> tuple[str, ...]:
        val = ctx.get(key)
        if val is None:
            return ()
        if isinstance(val, (list, tuple)):
            return tuple(str(v) for v in val)
        return (str(val),)

    snapshot = TaskStateSnapshot(
        objectives=_to_str_tuple(_OBJECTIVES_KEY),
        todos=_to_str_tuple(_TODOS_KEY),
        blockers=_to_str_tuple(_BLOCKERS_KEY),
        last_valid_result=ctx.get(_LAST_RESULT_KEY, {}),
        pending_approvals=_to_str_tuple(_PENDING_KEY),
    )

    logger.debug(
        "purposeful_compact_extracted",
        objectives_count=len(snapshot.objectives),
        todos_count=len(snapshot.todos),
        blockers_count=len(snapshot.blockers),
    )

    return snapshot


def render_task_state_text(snapshot: TaskStateSnapshot) -> str:
    """Render TaskStateSnapshot as text for injection into compaction prompt."""
    sections: list[str] = []
    if snapshot.objectives:
        sections.append("Objectives: " + "; ".join(snapshot.objectives))
    if snapshot.todos:
        sections.append("TODOs: " + "; ".join(snapshot.todos))
    if snapshot.blockers:
        sections.append("Blockers: " + "; ".join(snapshot.blockers))
    if snapshot.last_valid_result:
        sections.append("Last valid result: " + str(snapshot.last_valid_result))
    if snapshot.pending_approvals:
        sections.append("Pending approvals: " + "; ".join(snapshot.pending_approvals))
    return "\n".join(sections) if sections else ""
