"""Procedure Runtime type definitions.

Aligned with ``design_docs/procedure_runtime.md`` and the P2-M2a plan.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from src.tools.base import ToolMode
from src.tools.context import ToolContext

# OpenAI function name constraint: a-zA-Z0-9, underscores, dashes; max 64 chars.
_FUNCTION_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# Synthetic runtime tool names that action ids must not collide with.
RESERVED_ACTION_IDS = frozenset({"procedure_enter"})


# ---------------------------------------------------------------------------
# Spec types (static, frozen)
# ---------------------------------------------------------------------------


class ActionSpec(BaseModel):
    """Single allowed action from one procedure state."""

    model_config = ConfigDict(frozen=True)

    tool: str
    to: str
    guard: str | None = None


class StateSpec(BaseModel):
    """Action map for one procedure state."""

    model_config = ConfigDict(frozen=True)

    actions: dict[str, ActionSpec] = Field(default_factory=dict)


class ProcedureSpec(BaseModel):
    """Static contract for a named procedure."""

    model_config = ConfigDict(frozen=True)

    id: str
    version: int
    summary: str
    entry_policy: Literal["explicit"]
    allowed_modes: frozenset[ToolMode]
    context_model: str
    initial_state: str
    states: dict[str, StateSpec]
    enter_guard: str | None = None
    soft_policies: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Guard types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GuardDecision:
    """Result of a procedure guard check."""

    allowed: bool
    code: str = ""
    detail: str = ""


# Guard callable types — sync or async, normalized at call site.
ProcedureEnterGuard = Callable[
    [dict[str, Any], ToolContext | None],
    GuardDecision | Awaitable[GuardDecision],
]

ProcedureActionGuard = Callable[
    ["ActiveProcedure", dict[str, Any], ToolContext | None],
    GuardDecision | Awaitable[GuardDecision],
]


# ---------------------------------------------------------------------------
# Active instance (runtime, frozen)
# ---------------------------------------------------------------------------


class ProcedureExecutionMetadata(BaseModel):
    """Execution context reserved for future multi-principal / shared-space.

    P2-M2a does NOT interpret these fields — they exist only to ensure
    future extensions face validated, bounded data instead of arbitrary JSON.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    actor: str | None = None
    principal_id: str | None = None
    publish_target: str | None = None
    visibility_intent: str | None = None
    shared_space_id: str | None = None


class ActiveProcedure(BaseModel):
    """Runtime state of a running procedure instance."""

    model_config = ConfigDict(frozen=True)

    instance_id: str
    session_id: str
    spec_id: str
    spec_version: int
    state: str
    context: dict[str, Any] = Field(default_factory=dict)
    execution_metadata: ProcedureExecutionMetadata = Field(
        default_factory=ProcedureExecutionMetadata,
    )
    revision: int = 0


# ---------------------------------------------------------------------------
# CAS conflict (return-value, not exception)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CasConflict:
    """Returned by store when optimistic CAS fails."""

    instance_id: str
    expected_revision: int
    actual_revision: int | None = None


# ---------------------------------------------------------------------------
# Prompt view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcedureView:
    """Minimal projection of an active procedure for prompt injection."""

    id: str
    version: int
    summary: str
    state: str
    revision: int
    allowed_actions: tuple[str, ...] = ()
    soft_policies: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _validate_function_name(name: str) -> str | None:
    """Return error message if *name* is not a valid OpenAI function name."""
    if not _FUNCTION_NAME_RE.match(name):
        return (
            f"Action id '{name}' does not match OpenAI function name "
            f"constraint (a-zA-Z0-9_-, max 64 chars)"
        )
    return None


# ---------------------------------------------------------------------------
# Virtual action tool schema builder
# ---------------------------------------------------------------------------


def build_virtual_action_schema(
    action_id: str,
    action: ActionSpec,
    tool_description: str,
    tool_parameters: dict[str, Any],
) -> dict[str, Any]:
    """Build an OpenAI function-calling schema entry for a virtual action."""
    return {
        "type": "function",
        "function": {
            "name": action_id,
            "description": f"[Procedure action] {tool_description}",
            "parameters": tool_parameters,
        },
    }


def build_procedure_view(
    spec: ProcedureSpec,
    active: ActiveProcedure,
) -> ProcedureView:
    """Derive a ProcedureView from spec + active instance."""
    current_state = spec.states.get(active.state)
    allowed = tuple(current_state.actions.keys()) if current_state else ()
    return ProcedureView(
        id=spec.id,
        version=spec.version,
        summary=spec.summary,
        state=active.state,
        revision=active.revision,
        allowed_actions=allowed,
        soft_policies=spec.soft_policies,
    )
