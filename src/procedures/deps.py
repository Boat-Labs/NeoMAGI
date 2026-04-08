"""ProcedureActionDeps — injected into ToolContext for procedure-only tools.

Uses TYPE_CHECKING guard to reference ActiveProcedure / ProcedureSpec
without creating a runtime circular import with tools/context.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.procedures.types import ActiveProcedure, ProcedureSpec


@dataclass(frozen=True)
class ProcedureActionDeps:
    """Supplementary deps injected into ToolContext for procedure-only tools.

    Non-procedure tools see ``None``; procedure-only tools read what they need.
    Constructed by ``tool_concurrency._run_procedure_action()`` from AgentLoop state.
    """

    active_procedure: ActiveProcedure
    spec: ProcedureSpec
    model_client: Any  # ModelClient — Any to avoid import cycle
    model: str
