"""Domain types for the builder work memory substrate (P2-M1c).

Defines BuilderTaskRecord -- the canonical artifact record for builder tasks.

Design notes:
- frozen=True for immutability (consistent with SkillSpec pattern).
- artifact_id uses uuid4 as V1 fallback; upgrade to UUIDv7 tracked as TODO.
- No PostgreSQL table -- artifact truth lives in workspace/artifacts/ (ADR 0055).
- bd/beads serves as task index + state + evidence pointer only.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class BuilderTaskRecord(BaseModel):
    """Immutable builder task artifact record.

    Maps 1:1 with a markdown artifact file in ``workspace/artifacts/``.
    The ``bead_id`` links to the bd issue index (optional, best-effort).
    """

    model_config = ConfigDict(frozen=True)

    artifact_id: str  # TODO: upgrade to UUIDv7 when available (ADR 0055)
    bead_id: str | None = None
    task_brief: str
    scope: str
    decision_snapshots: tuple[str, ...] = ()
    todo_items: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    validation_summary: str | None = None
    promote_candidates: tuple[str, ...] = ()
    next_recommended_action: str | None = None
