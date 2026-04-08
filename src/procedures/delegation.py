"""DelegationTool + role-aware guard helpers for multi-agent runtime (P2-M2b Slice E).

DelegationTool is a procedure-only BaseTool that delegates work to a WorkerExecutor.
Worker results are stored in the ``_pending_handoffs`` staging area, not merged into
visible context (D4 — publish is the only path to user-level continuity).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from src.procedures.handoff import HandoffPacketBuilder, WorkerResult
from src.procedures.roles import DEFAULT_ROLE_SPECS, AgentRole, RoleSpec
from src.procedures.types import GuardDecision
from src.procedures.worker import WorkerExecutor
from src.tools.base import BaseTool, ToolMode

if TYPE_CHECKING:
    from src.tools.context import ToolContext

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Role-aware guard helpers
# ---------------------------------------------------------------------------


def require_role(actor: AgentRole | None, required: AgentRole) -> GuardDecision:
    """Guard helper: check that the current actor has the required role."""
    if actor is None:
        return GuardDecision(allowed=False, code="NO_ACTOR", detail="No actor in context")
    if actor != required:
        return GuardDecision(
            allowed=False,
            code="ROLE_DENIED",
            detail=f"Required role '{required}', got '{actor}'",
        )
    return GuardDecision(allowed=True)


# ---------------------------------------------------------------------------
# DelegationTool
# ---------------------------------------------------------------------------


class DelegationTool(BaseTool):
    """Procedure-only tool that delegates a subtask to a WorkerExecutor.

    Reads procedure context from ProcedureActionDeps (D8).
    Writes worker result to ``_pending_handoffs`` staging area.
    """

    def __init__(self, tool_registry: Any = None) -> None:
        self._tool_registry = tool_registry

    @property
    def name(self) -> str:
        return "procedure_delegate"

    @property
    def description(self) -> str:
        return "Delegate a subtask to a worker agent"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "task_brief": {
                    "type": "string",
                    "description": "What the worker should do",
                },
                "constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "What the worker must NOT do",
                },
                "include_keys": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Context keys to include in handoff packet",
                },
                "evidence": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Facts from primary context",
                },
                "open_questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "What the worker should figure out",
                },
            },
            "required": ["task_brief"],
        }

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset()

    @property
    def is_procedure_only(self) -> bool:
        return True

    async def execute(self, arguments: dict, context: ToolContext | None = None) -> dict:
        if context is None or context.procedure_deps is None:
            return {"ok": False, "error_code": "DELEGATION_NO_PROCEDURE_DEPS"}

        deps = context.procedure_deps
        task_brief = arguments.get("task_brief", "")
        constraints = tuple(arguments.get("constraints", ()))
        include_keys = tuple(arguments.get("include_keys", ()))
        evidence = tuple(arguments.get("evidence", ()))
        open_questions = tuple(arguments.get("open_questions", ()))

        # Build handoff packet
        builder = HandoffPacketBuilder(include_keys=include_keys)
        try:
            packet = builder.build(
                active=deps.active_procedure,
                spec=deps.spec,
                target_role=AgentRole.worker,
                task_brief=task_brief,
                constraints=constraints,
                evidence=evidence,
                open_questions=open_questions,
            )
        except ValueError as exc:
            return {"ok": False, "error_code": "DELEGATION_PACKET_ERROR", "detail": str(exc)}

        logger.info(
            "delegation_started",
            handoff_id=packet.handoff_id,
            target_role=packet.target_role,
            task_brief_len=len(task_brief),
        )

        # Create worker executor on-the-fly (D8)
        worker_role = DEFAULT_ROLE_SPECS.get(AgentRole.worker, RoleSpec(
            role=AgentRole.worker,
            allowed_tool_groups=frozenset(),
        ))
        registry = self._tool_registry or deps.model_client  # fallback: unused in test
        if self._tool_registry is None:
            # Minimal fallback for tests — real usage gets registry from __init__
            from src.tools.registry import ToolRegistry
            registry = ToolRegistry()

        worker = WorkerExecutor(
            model_client=deps.model_client,
            tool_registry=registry if hasattr(registry, "list_tools") else registry,
            role_spec=worker_role,
            model=deps.model,
            scope_key=context.scope_key if context else "main",
            session_id=context.session_id if context else "main",
        )

        # Execute worker
        worker_result: WorkerResult = await worker.execute(packet)

        logger.info(
            "delegation_completed",
            handoff_id=packet.handoff_id,
            ok=worker_result.ok,
            iterations_used=worker_result.iterations_used,
        )

        # Worker failure: don't stage, return error for model to retry.
        # M2a's apply_action() ignores context_patch on ok=False anyway,
        # so staging failed results would be a no-op in runtime.
        if not worker_result.ok:
            return {
                "ok": False,
                "handoff_id": packet.handoff_id,
                "worker_ok": False,
                "iterations_used": worker_result.iterations_used,
                "error_code": worker_result.error_code or "DELEGATION_WORKER_FAILED",
                "error_detail": worker_result.error_detail,
            }

        # Read-modify-write _pending_handoffs (P2-5r2: full dict for shallow merge)
        current_handoffs = dict(deps.active_procedure.context.get("_pending_handoffs", {}))
        current_handoffs[packet.handoff_id] = worker_result.model_dump()

        return {
            "ok": True,
            "handoff_id": packet.handoff_id,
            "worker_ok": True,
            "iterations_used": worker_result.iterations_used,
            "context_patch": {"_pending_handoffs": current_handoffs},
        }
