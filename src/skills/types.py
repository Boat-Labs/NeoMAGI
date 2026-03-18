"""Domain types for the skill runtime layer (P2-M1b).

Defines: task classification, skill specifications, evidence,
resolved views, task framing, outcomes, and the registry protocol.

Design notes:
- SkillSpec is the current-state store record; it has NO ``status`` field.
  Lifecycle status lives exclusively in the governance ledger
  (``skill_spec_versions``), preserving the current-state / ledger split.
- All Pydantic models use ``frozen=True`` for immutability.
- ``SkillRegistry`` is a Protocol so the concrete implementation can be
  injected (adapter pattern, consistent with growth kernel).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Task classification
# ---------------------------------------------------------------------------


class TaskType(StrEnum):
    """High-level task type classification for skill matching."""

    research = "research"
    create = "create"
    edit = "edit"
    debug = "debug"
    chat = "chat"
    unknown = "unknown"


# ---------------------------------------------------------------------------
# Skill specification (current-state store)
# ---------------------------------------------------------------------------


class SkillSpec(BaseModel):
    """Immutable skill specification record.

    Maps 1:1 with the ``skill_specs`` DB table.
    No ``status`` field -- lifecycle governance is in ``skill_spec_versions``.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    capability: str
    version: int
    summary: str
    activation: str
    activation_tags: tuple[str, ...] = ()
    preconditions: tuple[str, ...] = ()
    delta: tuple[str, ...] = ()
    tool_preferences: tuple[str, ...] = ()
    escalation_rules: tuple[str, ...] = ()
    exchange_policy: str = "local_only"
    disabled: bool = False


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------


class SkillEvidence(BaseModel):
    """Runtime evidence accumulated for a skill.

    ``last_validated_at`` is ``datetime | None`` to align with
    the ``TIMESTAMPTZ`` column in the DB.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    success_count: int = 0
    failure_count: int = 0
    last_validated_at: datetime | None = None
    positive_patterns: tuple[str, ...] = ()
    negative_patterns: tuple[str, ...] = ()
    known_breakages: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Resolved view (injected into prompt at runtime)
# ---------------------------------------------------------------------------


class ResolvedSkillView(BaseModel):
    """Resolved skill view projected into the agent prompt."""

    model_config = ConfigDict(frozen=True)

    llm_delta: tuple[str, ...] = ()
    runtime_hints: tuple[str, ...] = ()
    escalation_signals: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Task framing
# ---------------------------------------------------------------------------


class TaskFrame(BaseModel):
    """Snapshot of the current task context for skill selection."""

    model_config = ConfigDict(frozen=True)

    task_type: TaskType = TaskType.unknown
    target_outcome: str | None = None
    risk: str | None = None
    channel: str | None = None
    current_mode: str = "chat_safe"
    current_procedure: str | None = None
    available_tools: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Tool result placeholder + task outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolResult:
    """Minimal tool execution result placeholder."""

    tool_name: str
    success: bool
    output: str = ""


@dataclass(frozen=True)
class TaskOutcome:
    """Outcome of a completed task execution cycle."""

    success: bool
    terminal_state: Literal[
        "assistant_response",
        "tool_failure",
        "guard_denied",
        "procedure_terminal",
        "max_iterations",
    ]
    tool_results: tuple[ToolResult, ...] = ()
    user_confirmed: bool = False
    failure_signals: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Registry protocol
# ---------------------------------------------------------------------------


class SkillRegistry(Protocol):
    """Abstract skill registry -- concrete implementation injected."""

    async def list_active(self) -> list[SkillSpec]: ...

    async def get_evidence(self, skill_ids: tuple[str, ...]) -> dict[str, SkillEvidence]: ...
