from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.procedures.deps import ProcedureActionDeps
    from src.procedures.roles import AgentRole


@dataclass(frozen=True)
class ToolContext:
    """Runtime context injected into tool execution by AgentLoop.

    scope_key: resolved by session_resolver (ADR 0034). Tools MUST NOT
    re-derive scope from session_id; they consume this value directly.
    session_id: current session identifier (for audit/logging).

    P2-M2b additions (all default None — backward compatible):
    actor: agent role executing this action (D8).
    handoff_id: links to delegation audit trail.
    procedure_deps: supplementary deps for procedure-only tools (D8).
    """

    scope_key: str = "main"
    session_id: str = "main"
    principal_id: str | None = None  # P2-M3a: authenticated principal
    actor: AgentRole | None = None
    handoff_id: str | None = None
    procedure_deps: ProcedureActionDeps | None = None
