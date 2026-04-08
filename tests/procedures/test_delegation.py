"""Tests for P2-M2b Slice E: DelegationTool + role guards + mode bypass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from src.procedures.delegation import DelegationTool, require_role
from src.procedures.deps import ProcedureActionDeps
from src.procedures.roles import AgentRole
from src.procedures.types import ActiveProcedure, GuardDecision
from src.tools.base import BaseTool, ToolGroup, ToolMode
from src.tools.context import ToolContext
from src.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeChatMessage:
    content: str | None = None
    tool_calls: list[dict[str, str]] | None = None


class _FakeModelClient:
    """Returns a single final answer."""

    async def chat_completion(self, messages, model, *, tools=None, temperature=None):
        return _FakeChatMessage(content='{"result": {"answer": 42}}')

    async def chat(self, messages, model, temperature=None):
        return '{"approved": true}'


def _make_active(**overrides) -> ActiveProcedure:
    defaults = dict(
        instance_id="inst-1",
        session_id="sess-1",
        spec_id="test.spec",
        spec_version=1,
        state="planning",
        context={"key_a": "val_a"},
        revision=0,
    )
    defaults.update(overrides)
    return ActiveProcedure(**defaults)


def _make_deps(active: ActiveProcedure | None = None) -> ProcedureActionDeps:
    return ProcedureActionDeps(
        active_procedure=active or _make_active(),
        spec=None,  # type: ignore[arg-type]
        model_client=_FakeModelClient(),
        model="test",
    )


# ---------------------------------------------------------------------------
# require_role
# ---------------------------------------------------------------------------


class TestRequireRole:
    def test_allowed(self):
        d = require_role(AgentRole.primary, AgentRole.primary)
        assert d.allowed is True

    def test_denied_wrong_role(self):
        d = require_role(AgentRole.worker, AgentRole.primary)
        assert d.allowed is False
        assert d.code == "ROLE_DENIED"

    def test_denied_none_actor(self):
        d = require_role(None, AgentRole.primary)
        assert d.allowed is False
        assert d.code == "NO_ACTOR"


# ---------------------------------------------------------------------------
# DelegationTool
# ---------------------------------------------------------------------------


class TestDelegationTool:
    def test_is_procedure_only(self):
        tool = DelegationTool()
        assert tool.is_procedure_only is True
        assert tool.allowed_modes == frozenset()

    @pytest.mark.asyncio
    async def test_no_deps_fail_closed(self):
        tool = DelegationTool()
        result = await tool.execute({"task_brief": "do something"}, context=None)
        assert result["ok"] is False
        assert result["error_code"] == "DELEGATION_NO_PROCEDURE_DEPS"

    @pytest.mark.asyncio
    async def test_successful_delegation(self):
        tool = DelegationTool(tool_registry=ToolRegistry())
        active = _make_active()
        deps = _make_deps(active)
        ctx = ToolContext(
            actor=AgentRole.primary,
            procedure_deps=deps,
        )
        result = await tool.execute(
            {"task_brief": "compute something", "include_keys": ["key_a"]},
            context=ctx,
        )
        assert result["ok"] is True
        assert result["handoff_id"]
        # Worker result should be in staging
        staging = result["context_patch"]["_pending_handoffs"]
        assert result["handoff_id"] in staging

    @pytest.mark.asyncio
    async def test_read_modify_write_preserves_existing(self):
        tool = DelegationTool(tool_registry=ToolRegistry())
        active = _make_active(context={
            "_pending_handoffs": {"prev-1": {"ok": True, "result": {}}},
        })
        deps = _make_deps(active)
        ctx = ToolContext(actor=AgentRole.primary, procedure_deps=deps)
        result = await tool.execute({"task_brief": "new task"}, context=ctx)
        staging = result["context_patch"]["_pending_handoffs"]
        assert "prev-1" in staging  # preserved
        assert len(staging) == 2  # prev-1 + new

    @pytest.mark.asyncio
    async def test_empty_brief_rejected(self):
        tool = DelegationTool(tool_registry=ToolRegistry())
        deps = _make_deps()
        ctx = ToolContext(actor=AgentRole.primary, procedure_deps=deps)
        result = await tool.execute({"task_brief": "   "}, context=ctx)
        assert result["ok"] is False
        assert result["error_code"] == "DELEGATION_PACKET_ERROR"


# ---------------------------------------------------------------------------
# D7 mode bypass in ProcedureRuntime
# ---------------------------------------------------------------------------


class _EmptyModesNonProcTool(BaseTool):
    """Tool with empty allowed_modes but NOT procedure-only (D7 safety test)."""

    @property
    def name(self) -> str:
        return "empty_modes_normal"

    @property
    def description(self) -> str:
        return "Normal tool with empty modes"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: dict, context=None) -> dict:
        return {"ok": True}


class TestModeBypassD7:
    def test_procedure_only_tool_skips_mode_check(self):
        """DelegationTool.is_procedure_only is True → would skip mode check in runtime."""
        tool = DelegationTool()
        assert tool.is_procedure_only is True

    def test_empty_modes_normal_tool_does_not_skip(self):
        """A tool with empty modes but is_procedure_only=False does NOT get bypass."""
        tool = _EmptyModesNonProcTool()
        assert tool.allowed_modes == frozenset()
        assert tool.is_procedure_only is False
        # In runtime.py, this tool would still fail the mode check

    def test_ambient_schema_excludes_procedure_only(self):
        """Procedure-only tools don't appear in ambient tool schema."""
        reg = ToolRegistry()
        reg.register(DelegationTool())
        # DelegationTool has empty modes → not in any mode's list
        for mode in ToolMode:
            tools = reg.list_tools(mode)
            assert not any(t.name == "procedure_delegate" for t in tools)
