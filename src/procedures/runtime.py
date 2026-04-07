"""Procedure Runtime — core executor for deterministic state transitions.

Provides ``enter_procedure()`` and ``apply_action()`` as the two main
entry points. All state-machine logic lives here; ``AgentLoop`` only
delegates.
"""

from __future__ import annotations

import inspect
import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog

from src.procedures.registry import ProcedureContextRegistry, ProcedureGuardRegistry
from src.procedures.result import normalize_tool_result
from src.procedures.types import (
    ActiveProcedure,
    CasConflict,
    GuardDecision,
    ProcedureExecutionMetadata,
)
from src.tools.base import ToolMode
from src.tools.context import ToolContext

if TYPE_CHECKING:
    from src.agent.guardrail import GuardCheckResult
    from src.procedures.registry import ProcedureSpecRegistry
    from src.procedures.store import ProcedureStore
    from src.tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Structured error codes (Slice G)
# ---------------------------------------------------------------------------

PROCEDURE_CONFLICT = "PROCEDURE_CONFLICT"
PROCEDURE_UNKNOWN = "PROCEDURE_UNKNOWN"
PROCEDURE_ACTION_DENIED = "PROCEDURE_ACTION_DENIED"
PROCEDURE_CAS_CONFLICT = "PROCEDURE_CAS_CONFLICT"
PROCEDURE_INVALID_PATCH = "PROCEDURE_INVALID_PATCH"
PROCEDURE_TOOL_UNAVAILABLE = "PROCEDURE_TOOL_UNAVAILABLE"
PROCEDURE_INVALID_ARGS = "PROCEDURE_INVALID_ARGS"


# ---------------------------------------------------------------------------
# ProcedureRuntime
# ---------------------------------------------------------------------------


class ProcedureRuntime:
    """Core executor for procedure lifecycle.

    Holds references to spec/context/guard registries, the procedure store
    and the tool registry. Does NOT hold a reference to ``AgentLoop``.
    """

    def __init__(
        self,
        spec_registry: ProcedureSpecRegistry,
        context_registry: ProcedureContextRegistry,
        guard_registry: ProcedureGuardRegistry,
        store: ProcedureStore,
        tool_registry: ToolRegistry,
    ) -> None:
        self._specs = spec_registry
        self._contexts = context_registry
        self._guards = guard_registry
        self._store = store
        self._tools = tool_registry

    # -----------------------------------------------------------------
    # Enter
    # -----------------------------------------------------------------

    async def enter_procedure(
        self,
        session_id: str,
        spec_id: str,
        initial_context: dict[str, Any] | None = None,
        execution_metadata: ProcedureExecutionMetadata | None = None,
        mode: ToolMode | None = None,
    ) -> ActiveProcedure | dict[str, Any]:
        """Create a new active procedure for *session_id*.

        Returns ``ActiveProcedure`` on success, or a structured error dict.
        """
        spec = self._specs.get(spec_id)
        if spec is None:
            return _error(PROCEDURE_UNKNOWN, f"Unknown procedure spec: {spec_id}")

        # 1. Mode check
        if mode is not None and mode not in spec.allowed_modes:
            return _error(
                PROCEDURE_ACTION_DENIED,
                f"Mode '{mode}' not allowed for procedure '{spec_id}'",
            )

        # 2. Single-active check
        existing = await self._store.get_active(session_id)
        if existing is not None:
            return _error(
                PROCEDURE_CONFLICT,
                f"Session '{session_id}' already has active procedure "
                f"'{existing.spec_id}' (instance {existing.instance_id})",
            )

        # 3. Resolve context model
        ctx_model = self._contexts.resolve(spec.context_model)
        if ctx_model is None:
            return _error(
                PROCEDURE_UNKNOWN,
                f"Context model '{spec.context_model}' not found",
            )

        # 4. Validate initial context
        ctx = initial_context or {}
        validation_error = _validate_context(ctx_model, ctx)
        if validation_error is not None:
            return _error(PROCEDURE_INVALID_PATCH, validation_error)

        # 5. Enter guard
        if spec.enter_guard:
            guard_fn = self._guards.resolve(spec.enter_guard)
            if guard_fn is None:
                return _error(PROCEDURE_UNKNOWN, f"Enter guard '{spec.enter_guard}' not found")
            tool_context = ToolContext(session_id=session_id)
            decision = await _run_guard(guard_fn, ctx, tool_context)
            if not decision.allowed:
                logger.info(
                    "procedure_enter_denied",
                    spec_id=spec_id,
                    guard=spec.enter_guard,
                    code=decision.code,
                    detail=decision.detail,
                )
                return _error(PROCEDURE_ACTION_DENIED, decision.detail, code=decision.code)

        # 6. Create instance
        meta = execution_metadata or ProcedureExecutionMetadata()
        active = ActiveProcedure(
            instance_id=f"proc_{uuid4().hex}",
            session_id=session_id,
            spec_id=spec_id,
            spec_version=spec.version,
            state=spec.initial_state,
            context=ctx,
            execution_metadata=meta,
            revision=0,
        )
        created = await self._store.create(active)
        logger.info(
            "procedure_entered",
            instance_id=created.instance_id,
            session_id=session_id,
            spec_id=spec_id,
            state=spec.initial_state,
        )
        return created

    # -----------------------------------------------------------------
    # Apply action
    # -----------------------------------------------------------------

    async def apply_action(
        self,
        instance_id: str,
        action_id: str,
        args_json: str,
        expected_revision: int,
        *,
        tool_context: ToolContext | None = None,
        guard_state: GuardCheckResult | None = None,
        mode: ToolMode | None = None,
    ) -> dict[str, Any]:
        """Execute a procedure action.

        Returns a structured result dict suitable for inclusion in the
        model transcript. On success, state transitions and revision bumps.
        """
        # 1. Load instance and spec
        active = await self._store.get(instance_id)
        if active is None:
            return _error(PROCEDURE_UNKNOWN, f"Procedure instance '{instance_id}' not found")

        spec = self._specs.get(active.spec_id)
        if spec is None:
            return _error(PROCEDURE_UNKNOWN, f"Procedure spec '{active.spec_id}' not found")

        # 2. Revision check
        if active.revision != expected_revision:
            logger.warning(
                "procedure_cas_conflict",
                instance_id=instance_id,
                expected=expected_revision,
                actual=active.revision,
            )
            return _cas_conflict_result(instance_id, expected_revision, active.revision)

        # 3. Action validity in current state
        current_state = spec.states.get(active.state)
        if current_state is None or action_id not in current_state.actions:
            return _error(
                PROCEDURE_ACTION_DENIED,
                f"Action '{action_id}' not allowed in state '{active.state}'",
            )
        action = current_state.actions[action_id]

        # 4. Parse args (fail-closed, matching ambient tool runner)
        args_dict, parse_error = _parse_args(args_json)
        if parse_error is not None:
            return _error(PROCEDURE_INVALID_ARGS, parse_error)

        # 5. Underlying tool exists and mode allows
        tool = self._tools.get(action.tool)
        if tool is None:
            return _error(
                PROCEDURE_TOOL_UNAVAILABLE,
                f"Underlying tool '{action.tool}' not found",
            )
        if mode is not None and not self._tools.check_mode(action.tool, mode):
            return _error(
                PROCEDURE_ACTION_DENIED,
                f"Tool '{action.tool}' not available in mode '{mode}'",
            )

        # 6. Existing mode / risk guard (before procedure guard)
        if guard_state is not None:
            from src.agent.guardrail import check_pre_tool_guard

            blocked = check_pre_tool_guard(guard_state, action.tool, tool.risk_level)
            if blocked is not None:
                logger.info(
                    "procedure_action_denied",
                    instance_id=instance_id,
                    action_id=action_id,
                    reason="mode_risk_guard",
                    error_code=blocked.error_code,
                )
                return _error(PROCEDURE_ACTION_DENIED, blocked.detail)

        # 7. Procedure action guard
        if action.guard:
            guard_fn = self._guards.resolve(action.guard)
            if guard_fn is None:
                return _error(PROCEDURE_UNKNOWN, f"Action guard '{action.guard}' not found")
            decision = await _run_guard(guard_fn, active, args_dict, tool_context)
            if not decision.allowed:
                logger.info(
                    "procedure_action_denied",
                    instance_id=instance_id,
                    action_id=action_id,
                    guard=action.guard,
                    code=decision.code,
                )
                return _error(PROCEDURE_ACTION_DENIED, decision.detail, code=decision.code)

        # 8. Execute underlying tool
        try:
            raw_result = await tool.execute(args_dict, tool_context)
        except Exception:
            logger.exception(
                "procedure_action_failed",
                instance_id=instance_id,
                action_id=action_id,
                tool=action.tool,
            )
            return _error(
                PROCEDURE_TOOL_UNAVAILABLE,
                f"Tool '{action.tool}' execution failed",
            )

        # 9. Normalize result
        try:
            result = normalize_tool_result(raw_result)
        except Exception as exc:
            logger.warning(
                "procedure_normalize_failed",
                instance_id=instance_id,
                action_id=action_id,
                error=str(exc),
            )
            return _error(PROCEDURE_INVALID_PATCH, f"Tool result normalization failed: {exc}")

        # 10. If tool reports failure, stay in current state
        if not result.ok:
            logger.info(
                "procedure_action_failed",
                instance_id=instance_id,
                action_id=action_id,
                tool=action.tool,
                ok=False,
            )
            return {
                "ok": False,
                "error_code": "PROCEDURE_TOOL_FAILURE",
                "instance_id": instance_id,
                "state": active.state,
                "revision": active.revision,
                **result.data,
            }

        # 11. Shallow-merge context_patch
        new_context = {**active.context, **result.context_patch}

        # 12. Validate merged context
        ctx_model = self._contexts.resolve(spec.context_model)
        if ctx_model is not None:
            validation_error = _validate_context(ctx_model, new_context)
            if validation_error is not None:
                logger.warning(
                    "procedure_invalid_patch",
                    instance_id=instance_id,
                    action_id=action_id,
                    error=validation_error,
                )
                return _error(PROCEDURE_INVALID_PATCH, validation_error)

        # 12. CAS write: transition state + bump revision
        target_state = action.to
        target_state_spec = spec.states.get(target_state)
        is_terminal = target_state_spec is not None and not target_state_spec.actions

        cas_result = await self._store.cas_update(
            instance_id,
            expected_revision,
            state=target_state,
            context=new_context,
            completed_at=is_terminal,
        )

        if isinstance(cas_result, CasConflict):
            logger.warning(
                "procedure_cas_conflict",
                instance_id=instance_id,
                expected=expected_revision,
                actual=cas_result.actual_revision,
            )
            return _cas_conflict_result(
                instance_id, expected_revision, cas_result.actual_revision,
            )

        # Success
        log_event = "procedure_completed" if is_terminal else "procedure_action_transitioned"
        logger.info(
            log_event,
            instance_id=instance_id,
            action_id=action_id,
            from_state=active.state,
            to_state=target_state,
            revision=cas_result.revision,
        )
        return {
            "ok": True,
            "instance_id": instance_id,
            "action_id": action_id,
            "from_state": active.state,
            "to_state": target_state,
            "revision": cas_result.revision,
            "completed": is_terminal,
            **result.data,
        }

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    async def load_active(self, session_id: str) -> ActiveProcedure | None:
        """Load the active procedure for a session (used by AgentLoop)."""
        return await self._store.get_active(session_id)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _run_guard(guard_fn: Any, *args: Any) -> GuardDecision:
    """Call a guard (sync or async) and return a GuardDecision."""
    result = guard_fn(*args)
    if inspect.isawaitable(result):
        result = await result
    return result


def _parse_args(args_json: str) -> tuple[dict[str, Any], str | None]:
    """Parse JSON args. Returns (args, error_message)."""
    try:
        parsed = json.loads(args_json)
    except (json.JSONDecodeError, TypeError) as exc:
        return {}, f"Invalid JSON arguments: {exc}"
    if not isinstance(parsed, dict):
        return {}, f"Expected dict arguments, got {type(parsed).__name__}"
    return parsed, None


def _validate_context(ctx_model: type, ctx: dict[str, Any]) -> str | None:
    """Validate context against a Pydantic model. Returns error string or None."""
    try:
        ctx_model.model_validate(ctx)
        return None
    except Exception as exc:
        return f"Context validation failed: {exc}"


def _error(error_code: str, message: str, *, code: str = "") -> dict[str, Any]:
    """Build a structured error result."""
    result: dict[str, Any] = {
        "ok": False,
        "error_code": error_code,
        "message": message,
    }
    if code:
        result["guard_code"] = code
    return result


def _cas_conflict_result(
    instance_id: str, expected: int, actual: int | None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "error_code": PROCEDURE_CAS_CONFLICT,
        "message": (
            f"CAS conflict: expected revision {expected}, "
            f"actual {actual}"
        ),
        "instance_id": instance_id,
        "expected_revision": expected,
        "actual_revision": actual,
        "retryable": True,
    }
