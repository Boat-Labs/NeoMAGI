"""Tests for procedure action barrier in tool concurrency."""

from __future__ import annotations

from src.agent.tool_concurrency import _build_execution_groups, _is_parallel_eligible
from src.tools.base import BaseTool, ToolMode
from src.tools.registry import ToolRegistry


class _ReadOnlyTool(BaseTool):
    """Tool eligible for parallel execution."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "read-only tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object"}

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.coding})

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_concurrency_safe(self) -> bool:
        return True

    async def execute(self, arguments: dict, context=None) -> dict:
        return {}


class TestProcedureActionBarrier:
    def test_procedure_action_never_parallel(self) -> None:
        """Procedure actions must act as barriers even if their underlying
        tool would be parallel-eligible."""
        reg = ToolRegistry()
        reg.register(_ReadOnlyTool("read_tool"))

        # "submit" is a procedure action id, not a real tool name
        action_ids = frozenset({"submit"})

        assert _is_parallel_eligible("read_tool", reg) is True
        assert _is_parallel_eligible("submit", reg, action_ids) is False

    def test_groups_with_procedure_action(self) -> None:
        """Procedure action should break parallel groups."""
        reg = ToolRegistry()
        reg.register(_ReadOnlyTool("r1"))
        reg.register(_ReadOnlyTool("r2"))

        tool_calls = [
            {"name": "r1", "id": "c1", "arguments": "{}"},
            {"name": "submit", "id": "c2", "arguments": "{}"},  # procedure action
            {"name": "r2", "id": "c3", "arguments": "{}"},
        ]
        action_ids = frozenset({"submit"})
        groups = _build_execution_groups(tool_calls, reg, action_ids)

        # r1 parallel, submit barrier, r2 parallel
        assert len(groups) == 3
        assert groups[0].parallel is True
        assert groups[0].tool_calls[0]["name"] == "r1"
        assert groups[1].parallel is False
        assert groups[1].tool_calls[0]["name"] == "submit"
        assert groups[2].parallel is True
        assert groups[2].tool_calls[0]["name"] == "r2"

    def test_groups_without_procedure_actions_unchanged(self) -> None:
        """When no procedure actions, behavior is unchanged."""
        reg = ToolRegistry()
        reg.register(_ReadOnlyTool("r1"))
        reg.register(_ReadOnlyTool("r2"))

        tool_calls = [
            {"name": "r1", "id": "c1", "arguments": "{}"},
            {"name": "r2", "id": "c2", "arguments": "{}"},
        ]
        groups = _build_execution_groups(tool_calls, reg)
        assert len(groups) == 1
        assert groups[0].parallel is True
        assert len(groups[0].tool_calls) == 2
