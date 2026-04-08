"""Handoff packet types and builder for multi-agent runtime (P2-M2b).

HandoffPacket is the ONLY context exchange surface between agents (D3).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.procedures.roles import AgentRole
from src.procedures.types import ActiveProcedure, ProcedureExecutionMetadata, ProcedureSpec

# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------

MAX_PACKET_BYTES = 32 * 1024  # 32 KB total serialized
MAX_TASK_BRIEF_CHARS = 4000
MAX_ITEM_CHARS = 500

# ---------------------------------------------------------------------------
# Handoff Packet
# ---------------------------------------------------------------------------


class HandoffPacket(BaseModel):
    """Bounded context exchange between agents (D3)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    handoff_id: str
    source_actor: AgentRole
    target_role: AgentRole
    task_brief: str
    constraints: tuple[str, ...] = ()
    current_state: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    execution_metadata: ProcedureExecutionMetadata = Field(
        default_factory=ProcedureExecutionMetadata,
    )

    @field_validator("task_brief")
    @classmethod
    def _task_brief_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("task_brief must not be empty")
        return v


# ---------------------------------------------------------------------------
# Worker / Review / TaskState results
# ---------------------------------------------------------------------------


class WorkerResult(BaseModel):
    """Structured result from WorkerExecutor."""

    model_config = ConfigDict(frozen=True)

    ok: bool
    result: dict[str, Any] = Field(default_factory=dict)
    evidence: tuple[str, ...] = ()
    open_questions: tuple[str, ...] = ()
    iterations_used: int = 0
    error_code: str = ""
    error_detail: str = ""


class ReviewResult(BaseModel):
    """Structured result from ReviewerExecutor."""

    model_config = ConfigDict(frozen=True)

    approved: bool
    concerns: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()


class TaskStateSnapshot(BaseModel):
    """Structured task state for purposeful compact (D5)."""

    model_config = ConfigDict(frozen=True)

    objectives: tuple[str, ...] = ()
    todos: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    last_valid_result: dict[str, Any] = Field(default_factory=dict)
    pending_approvals: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# HandoffPacketBuilder
# ---------------------------------------------------------------------------


class HandoffPacketBuilder:
    """Build a bounded HandoffPacket from procedure context.

    The builder extracts only ``include_keys`` from the active procedure
    context, enforces per-field and total size limits, and auto-fills
    handoff_id + source_actor + execution_metadata.
    """

    def __init__(
        self,
        include_keys: tuple[str, ...] = (),
    ) -> None:
        self._include_keys = include_keys

    def build(
        self,
        *,
        active: ActiveProcedure,
        spec: ProcedureSpec,
        target_role: AgentRole,
        task_brief: str,
        constraints: tuple[str, ...] = (),
        evidence: tuple[str, ...] = (),
        open_questions: tuple[str, ...] = (),
    ) -> HandoffPacket:
        """Build and validate a HandoffPacket.

        Raises ``ValueError`` if the packet exceeds the 32 KB limit
        or any field violates per-item constraints.
        """
        current_state = {
            k: v for k, v in active.context.items() if k in self._include_keys
        }

        _validate_item_lengths("constraints", constraints)
        _validate_item_lengths("evidence", evidence)
        _validate_item_lengths("open_questions", open_questions)
        if len(task_brief) > MAX_TASK_BRIEF_CHARS:
            raise ValueError(
                f"task_brief exceeds {MAX_TASK_BRIEF_CHARS} chars: {len(task_brief)}"
            )

        packet = HandoffPacket(
            handoff_id=str(uuid4()),
            source_actor=AgentRole.primary,
            target_role=target_role,
            task_brief=task_brief,
            constraints=constraints,
            current_state=current_state,
            evidence=evidence,
            open_questions=open_questions,
            execution_metadata=active.execution_metadata,
        )

        serialized = json.dumps(packet.model_dump(), ensure_ascii=False).encode("utf-8")
        if len(serialized) > MAX_PACKET_BYTES:
            raise ValueError(
                f"HandoffPacket exceeds {MAX_PACKET_BYTES} bytes: {len(serialized)}"
            )

        return packet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_item_lengths(field_name: str, items: tuple[str, ...]) -> None:
    for i, item in enumerate(items):
        if len(item) > MAX_ITEM_CHARS:
            raise ValueError(
                f"{field_name}[{i}] exceeds {MAX_ITEM_CHARS} chars: {len(item)}"
            )
