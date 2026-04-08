"""Agent role types and role specifications for multi-agent runtime (P2-M2b).

Roles are execution-oriented (ADR 0047), not personality-oriented.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from src.tools.base import ToolGroup


class AgentRole(StrEnum):
    """Execution-oriented agent roles (ADR 0047)."""

    primary = "primary"
    worker = "worker"
    reviewer = "reviewer"


class RoleSpec(BaseModel):
    """Capability contract for an agent role."""

    model_config = ConfigDict(frozen=True)

    role: AgentRole
    allowed_tool_groups: frozenset[ToolGroup]
    can_publish: bool = False
    can_delegate: bool = False
    max_iterations: int = 5


# V1 defaults ----------------------------------------------------------------

MAX_PRIMARY_ITERATIONS = 10  # mirrors agent.agent.MAX_TOOL_ITERATIONS

DEFAULT_ROLE_SPECS: dict[AgentRole, RoleSpec] = {
    AgentRole.primary: RoleSpec(
        role=AgentRole.primary,
        allowed_tool_groups=frozenset(ToolGroup),
        can_publish=True,
        can_delegate=True,
        max_iterations=MAX_PRIMARY_ITERATIONS,
    ),
    AgentRole.worker: RoleSpec(
        role=AgentRole.worker,
        allowed_tool_groups=frozenset({ToolGroup.code, ToolGroup.world}),
        can_publish=False,
        can_delegate=False,
        max_iterations=5,
    ),
    AgentRole.reviewer: RoleSpec(
        role=AgentRole.reviewer,
        allowed_tool_groups=frozenset({ToolGroup.code}),
        can_publish=False,
        can_delegate=False,
        max_iterations=3,
    ),
}
