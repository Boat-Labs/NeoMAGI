"""Tests for P2-M2b Slice A: role types, ToolContext extension, ProcedureActionDeps, BaseTool marker."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from src.procedures.roles import (
    DEFAULT_ROLE_SPECS,
    AgentRole,
    RoleSpec,
)
from src.tools.base import BaseTool, ToolGroup, ToolMode


# ---------------------------------------------------------------------------
# Import smoke test (D8 — no circular import)
# ---------------------------------------------------------------------------


def test_import_no_circular_dependency():
    """ProcedureActionDeps and ToolContext can coexist without import cycle."""
    from src.procedures.deps import ProcedureActionDeps  # noqa: F401
    from src.tools.context import ToolContext  # noqa: F401


# ---------------------------------------------------------------------------
# AgentRole
# ---------------------------------------------------------------------------


class TestAgentRole:
    def test_values(self):
        assert AgentRole.primary == "primary"
        assert AgentRole.worker == "worker"
        assert AgentRole.reviewer == "reviewer"

    def test_construct_from_string(self):
        assert AgentRole("primary") is AgentRole.primary

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            AgentRole("unknown")


# ---------------------------------------------------------------------------
# RoleSpec
# ---------------------------------------------------------------------------


class TestRoleSpec:
    def test_frozen(self):
        spec = RoleSpec(
            role=AgentRole.worker,
            allowed_tool_groups=frozenset({ToolGroup.code}),
        )
        with pytest.raises(Exception):  # pydantic ValidationError on frozen
            spec.role = AgentRole.primary  # type: ignore[misc]

    def test_defaults(self):
        spec = RoleSpec(
            role=AgentRole.reviewer,
            allowed_tool_groups=frozenset(),
        )
        assert spec.can_publish is False
        assert spec.can_delegate is False
        assert spec.max_iterations == 5


# ---------------------------------------------------------------------------
# DEFAULT_ROLE_SPECS
# ---------------------------------------------------------------------------


class TestDefaultRoleSpecs:
    def test_all_roles_covered(self):
        for role in AgentRole:
            assert role in DEFAULT_ROLE_SPECS

    def test_primary_can_publish_and_delegate(self):
        spec = DEFAULT_ROLE_SPECS[AgentRole.primary]
        assert spec.can_publish is True
        assert spec.can_delegate is True

    def test_worker_cannot_publish_or_delegate(self):
        spec = DEFAULT_ROLE_SPECS[AgentRole.worker]
        assert spec.can_publish is False
        assert spec.can_delegate is False

    def test_reviewer_cannot_publish_or_delegate(self):
        spec = DEFAULT_ROLE_SPECS[AgentRole.reviewer]
        assert spec.can_publish is False
        assert spec.can_delegate is False


# ---------------------------------------------------------------------------
# ToolContext backward compatibility
# ---------------------------------------------------------------------------


class TestToolContextBackwardCompat:
    def test_default_new_fields_are_none(self):
        from src.tools.context import ToolContext

        ctx = ToolContext()
        assert ctx.actor is None
        assert ctx.handoff_id is None
        assert ctx.procedure_deps is None

    def test_existing_fields_unchanged(self):
        from src.tools.context import ToolContext

        ctx = ToolContext(scope_key="test", session_id="sess1")
        assert ctx.scope_key == "test"
        assert ctx.session_id == "sess1"

    def test_actor_accepts_agent_role(self):
        from src.tools.context import ToolContext

        ctx = ToolContext(actor=AgentRole.primary)
        assert ctx.actor == AgentRole.primary


# ---------------------------------------------------------------------------
# ProcedureActionDeps
# ---------------------------------------------------------------------------


class TestProcedureActionDeps:
    def test_frozen(self):
        from src.procedures.deps import ProcedureActionDeps

        deps = ProcedureActionDeps(
            active_procedure=None,  # type: ignore[arg-type]
            spec=None,  # type: ignore[arg-type]
            model_client=None,
            model="gpt-4o-mini",
        )
        with pytest.raises(FrozenInstanceError):
            deps.model = "other"  # type: ignore[misc]

    def test_fields(self):
        from src.procedures.deps import ProcedureActionDeps

        deps = ProcedureActionDeps(
            active_procedure="ap",  # type: ignore[arg-type]
            spec="sp",  # type: ignore[arg-type]
            model_client="mc",
            model="m",
        )
        assert deps.active_procedure == "ap"
        assert deps.spec == "sp"
        assert deps.model_client == "mc"
        assert deps.model == "m"


# ---------------------------------------------------------------------------
# BaseTool.is_procedure_only
# ---------------------------------------------------------------------------


class _DummyTool(BaseTool):
    @property
    def name(self) -> str:
        return "dummy"

    @property
    def description(self) -> str:
        return "dummy"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: dict, context=None) -> dict:
        return {}


class _ProcedureOnlyTool(_DummyTool):
    @property
    def is_procedure_only(self) -> bool:
        return True

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset()


class TestBaseTool:
    def test_default_is_procedure_only_false(self):
        tool = _DummyTool()
        assert tool.is_procedure_only is False

    def test_override_is_procedure_only_true(self):
        tool = _ProcedureOnlyTool()
        assert tool.is_procedure_only is True

    def test_empty_modes_does_not_imply_procedure_only(self):
        """A tool with empty allowed_modes but is_procedure_only=False
        should NOT be treated as procedure-only (D7 safety)."""
        tool = _DummyTool()
        assert tool.allowed_modes == frozenset()
        assert tool.is_procedure_only is False


# ---------------------------------------------------------------------------
# ProcedureExecutionMetadata actor semantics
# ---------------------------------------------------------------------------


class TestExecutionMetadataActor:
    def test_actor_roundtrip(self):
        from src.procedures.types import ProcedureExecutionMetadata

        meta = ProcedureExecutionMetadata(actor=AgentRole.primary.value)
        assert meta.actor == "primary"
        assert AgentRole(meta.actor) is AgentRole.primary
