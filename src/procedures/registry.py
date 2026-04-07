"""Procedure registries and static validation.

Three registries:
- ``ProcedureSpecRegistry``: stores validated ``ProcedureSpec`` by id.
- ``ProcedureContextRegistry``: maps context_model names to Pydantic models.
- ``ProcedureGuardRegistry``: maps guard names to enter/action guard callables.
"""

from __future__ import annotations

from typing import Any

import structlog
from pydantic import BaseModel

from src.procedures.types import (
    RESERVED_ACTION_IDS,
    ProcedureActionGuard,
    ProcedureEnterGuard,
    ProcedureSpec,
    _validate_function_name,
)
from src.tools.registry import ToolRegistry

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Context model registry
# ---------------------------------------------------------------------------


class ProcedureContextRegistry:
    """Maps ``context_model`` string keys to Pydantic BaseModel classes."""

    def __init__(self) -> None:
        self._models: dict[str, type[BaseModel]] = {}

    def register(self, name: str, model: type[BaseModel]) -> None:
        if name in self._models:
            raise ValueError(f"Context model already registered: {name}")
        self._models[name] = model

    def resolve(self, name: str) -> type[BaseModel] | None:
        return self._models.get(name)


# ---------------------------------------------------------------------------
# Guard registry
# ---------------------------------------------------------------------------


class ProcedureGuardRegistry:
    """Maps guard string keys to procedure guard callables.

    Stores both enter guards and action guards in the same namespace.
    Guard callables may be sync or async — the runtime normalizes at call site.
    """

    def __init__(self) -> None:
        self._guards: dict[str, ProcedureEnterGuard | ProcedureActionGuard] = {}

    def register(self, name: str, guard: ProcedureEnterGuard | ProcedureActionGuard) -> None:
        if name in self._guards:
            raise ValueError(f"Guard already registered: {name}")
        self._guards[name] = guard

    def resolve(self, name: str) -> ProcedureEnterGuard | ProcedureActionGuard | None:
        return self._guards.get(name)


# ---------------------------------------------------------------------------
# Spec registry
# ---------------------------------------------------------------------------


class ProcedureSpecRegistry:
    """Registry for validated ``ProcedureSpec`` instances.

    When constructed with *tool_registry*, *context_registry*, and
    *guard_registry*, every ``register()`` call runs static validation
    and raises ``ValueError`` on any error (fail-closed).

    The zero-argument constructor (no validation) exists only as a
    test-only convenience.  Production paths MUST supply all three
    registries — see ``gateway/app.py::_build_procedure_runtime``.
    """

    def __init__(
        self,
        tool_registry: ToolRegistry | None = None,
        context_registry: ProcedureContextRegistry | None = None,
        guard_registry: ProcedureGuardRegistry | None = None,
    ) -> None:
        self._specs: dict[str, ProcedureSpec] = {}
        self._tool_registry = tool_registry
        self._context_registry = context_registry
        self._guard_registry = guard_registry

    def register(self, spec: ProcedureSpec) -> None:
        """Register a spec after static validation. Raises on any error."""
        if spec.id in self._specs:
            raise ValueError(f"ProcedureSpec already registered: {spec.id}")
        if self._tool_registry and self._context_registry and self._guard_registry:
            errors = validate_procedure_spec(
                spec, self._tool_registry, self._context_registry, self._guard_registry,
            )
            if errors:
                raise ValueError(
                    f"ProcedureSpec '{spec.id}' failed validation: " + "; ".join(errors)
                )
        self._specs[spec.id] = spec
        logger.info("procedure_spec_registered", spec_id=spec.id, version=spec.version)

    def get(self, spec_id: str) -> ProcedureSpec | None:
        return self._specs.get(spec_id)

    def list_specs(self) -> list[ProcedureSpec]:
        return list(self._specs.values())


# ---------------------------------------------------------------------------
# Static validation
# ---------------------------------------------------------------------------


def validate_procedure_spec(
    spec: ProcedureSpec,
    tool_registry: ToolRegistry,
    context_registry: ProcedureContextRegistry,
    guard_registry: ProcedureGuardRegistry,
) -> list[str]:
    """Validate a ProcedureSpec against registries. Returns list of errors (empty = valid)."""
    errors: list[str] = []

    # 1. initial_state must exist in states
    if spec.initial_state not in spec.states:
        errors.append(f"initial_state '{spec.initial_state}' not in states")

    # 2. context_model must be resolvable
    if context_registry.resolve(spec.context_model) is None:
        errors.append(f"context_model '{spec.context_model}' not found in context registry")

    # 3. enter_guard must be resolvable
    if spec.enter_guard and guard_registry.resolve(spec.enter_guard) is None:
        errors.append(f"enter_guard '{spec.enter_guard}' not found in guard registry")

    # 4. entry_policy must be 'explicit'
    if spec.entry_policy != "explicit":
        errors.append(f"entry_policy must be 'explicit', got '{spec.entry_policy}'")

    # 5. Collect all ambient tool names for collision check
    ambient_tool_names = _collect_ambient_tool_names(tool_registry)

    # 6. Validate each state and its actions
    all_action_ids: set[str] = set()
    for state_name, state_spec in spec.states.items():
        for action_id, action in state_spec.actions.items():
            _validate_action(
                action_id, action, state_name, spec,
                tool_registry, guard_registry, ambient_tool_names,
                all_action_ids, errors,
            )

    return errors


def _collect_ambient_tool_names(tool_registry: ToolRegistry) -> set[str]:
    """Collect all registered ambient tool names for collision detection."""
    names: set[str] = set()
    from src.tools.base import ToolMode

    for mode in ToolMode:
        for tool in tool_registry.list_tools(mode):
            names.add(tool.name)
    return names


def _validate_action(
    action_id: str,
    action: Any,
    state_name: str,
    spec: ProcedureSpec,
    tool_registry: ToolRegistry,
    guard_registry: ProcedureGuardRegistry,
    ambient_tool_names: set[str],
    all_action_ids: set[str],
    errors: list[str],
) -> None:
    """Validate a single action within a state."""
    prefix = f"state '{state_name}', action '{action_id}'"

    # Action id must be valid OpenAI function name
    fn_error = _validate_function_name(action_id)
    if fn_error:
        errors.append(f"{prefix}: {fn_error}")

    # Action id must not collide with reserved names
    if action_id in RESERVED_ACTION_IDS:
        errors.append(f"{prefix}: action id collides with reserved name '{action_id}'")

    # Action id must not collide with ambient tool names
    if action_id in ambient_tool_names:
        errors.append(
            f"{prefix}: action id '{action_id}' collides with "
            f"registered ambient tool name"
        )

    # Action id uniqueness across all states (for virtual schema dedup)
    all_action_ids.add(action_id)

    # Target state must exist
    if action.to not in spec.states:
        errors.append(f"{prefix}: target state '{action.to}' not in states")

    # Underlying tool must exist in registry
    if tool_registry.get(action.tool) is None:
        errors.append(f"{prefix}: tool '{action.tool}' not found in tool registry")

    # Action guard must be resolvable
    if action.guard and guard_registry.resolve(action.guard) is None:
        errors.append(f"{prefix}: guard '{action.guard}' not found in guard registry")
