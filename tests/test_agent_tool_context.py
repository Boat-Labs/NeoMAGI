"""Tests for AgentLoop._execute_tool with ToolContext (Phase 0).

Covers:
- ToolContext construction and scope_key propagation
- Pre-tool guard integration
- Concurrent isolation (scope_key as local variable, not instance field)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agent.agent import AgentLoop
from src.agent.guardrail import GuardCheckResult
from src.tools.base import BaseTool, RiskLevel, ToolGroup, ToolMode
from src.tools.context import ToolContext
from src.tools.registry import ToolRegistry


class _SpyTool(BaseTool):
    """Tool that captures the context it receives."""

    def __init__(self) -> None:
        self.last_context: ToolContext | None = None
        self.call_count = 0

    @property
    def name(self) -> str:
        return "spy_tool"

    @property
    def description(self) -> str:
        return "Spy tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def group(self) -> ToolGroup:
        return ToolGroup.world

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.chat_safe})

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.low

    async def execute(
        self, arguments: dict, context: ToolContext | None = None
    ) -> dict:
        self.last_context = context
        self.call_count += 1
        return {"ok": True}


class _HighRiskSpyTool(BaseTool):
    """High-risk spy tool for guard testing."""

    def __init__(self) -> None:
        self.call_count = 0

    @property
    def name(self) -> str:
        return "high_risk_spy"

    @property
    def description(self) -> str:
        return "High-risk spy"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def group(self) -> ToolGroup:
        return ToolGroup.memory

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.chat_safe})

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.high

    async def execute(
        self, arguments: dict, context: ToolContext | None = None
    ) -> dict:
        self.call_count += 1
        return {"ok": True}


def _make_agent_loop(tmp_path: Path, registry: ToolRegistry) -> AgentLoop:
    model_client = MagicMock()
    session_manager = MagicMock()
    return AgentLoop(
        model_client=model_client,
        session_manager=session_manager,
        workspace_dir=tmp_path,
        tool_registry=registry,
    )


class TestExecuteToolContextPropagation:
    @pytest.mark.asyncio
    async def test_context_is_passed_to_tool(self, tmp_path: Path) -> None:
        spy = _SpyTool()
        registry = ToolRegistry()
        registry.register(spy)
        loop = _make_agent_loop(tmp_path, registry)

        guard = GuardCheckResult(passed=True)
        await loop._execute_tool(
            "spy_tool",
            json.dumps({"key": "val"}),
            scope_key="main",
            session_id="sess-1",
            guard_state=guard,
        )

        assert spy.last_context is not None
        assert spy.last_context.scope_key == "main"
        assert spy.last_context.session_id == "sess-1"

    @pytest.mark.asyncio
    async def test_context_none_not_passed(self, tmp_path: Path) -> None:
        """Even though we always construct context, verify it's a ToolContext."""
        spy = _SpyTool()
        registry = ToolRegistry()
        registry.register(spy)
        loop = _make_agent_loop(tmp_path, registry)

        guard = GuardCheckResult(passed=True)
        await loop._execute_tool(
            "spy_tool",
            json.dumps({}),
            scope_key="test-scope",
            session_id="s2",
            guard_state=guard,
        )
        assert isinstance(spy.last_context, ToolContext)
        assert spy.last_context.scope_key == "test-scope"


class TestPreToolGuardInExecuteTool:
    @pytest.mark.asyncio
    async def test_high_risk_blocked_when_guard_failed(self, tmp_path: Path) -> None:
        spy = _HighRiskSpyTool()
        registry = ToolRegistry()
        registry.register(spy)
        loop = _make_agent_loop(tmp_path, registry)

        failed_guard = GuardCheckResult(
            passed=False,
            missing_anchors=["anchor"],
            error_code="GUARD_ANCHOR_MISSING",
            detail="test",
        )
        result = await loop._execute_tool(
            "high_risk_spy",
            json.dumps({}),
            scope_key="main",
            session_id="s1",
            guard_state=failed_guard,
        )
        assert result["error_code"] == "GUARD_ANCHOR_MISSING"
        assert spy.call_count == 0  # tool was NOT executed

    @pytest.mark.asyncio
    async def test_low_risk_allowed_when_guard_failed(self, tmp_path: Path) -> None:
        spy = _SpyTool()
        registry = ToolRegistry()
        registry.register(spy)
        loop = _make_agent_loop(tmp_path, registry)

        failed_guard = GuardCheckResult(
            passed=False,
            missing_anchors=["anchor"],
            error_code="GUARD_ANCHOR_MISSING",
            detail="test",
        )
        result = await loop._execute_tool(
            "spy_tool",
            json.dumps({}),
            scope_key="main",
            session_id="s1",
            guard_state=failed_guard,
        )
        assert result["ok"] is True
        assert spy.call_count == 1

    @pytest.mark.asyncio
    async def test_high_risk_allowed_when_guard_passed(self, tmp_path: Path) -> None:
        spy = _HighRiskSpyTool()
        registry = ToolRegistry()
        registry.register(spy)
        loop = _make_agent_loop(tmp_path, registry)

        passed_guard = GuardCheckResult(passed=True)
        result = await loop._execute_tool(
            "high_risk_spy",
            json.dumps({}),
            scope_key="main",
            session_id="s1",
            guard_state=passed_guard,
        )
        assert result["ok"] is True
        assert spy.call_count == 1


class TestConcurrentIsolation:
    """Verify scope_key does not leak between concurrent requests."""

    @pytest.mark.asyncio
    async def test_concurrent_scope_keys_isolated(self, tmp_path: Path) -> None:
        spy = _SpyTool()
        registry = ToolRegistry()
        registry.register(spy)
        loop = _make_agent_loop(tmp_path, registry)
        guard = GuardCheckResult(passed=True)

        contexts: list[ToolContext | None] = []

        async def call_with_scope(scope: str) -> None:
            await loop._execute_tool(
                "spy_tool",
                json.dumps({}),
                scope_key=scope,
                session_id=f"sess-{scope}",
                guard_state=guard,
            )
            contexts.append(spy.last_context)

        # Run two "concurrent" calls (serial but simulating different scope_keys)
        await call_with_scope("scope-A")
        ctx_a = contexts[-1]
        await call_with_scope("scope-B")
        ctx_b = contexts[-1]

        assert ctx_a is not None
        assert ctx_b is not None
        assert ctx_a.scope_key == "scope-A"
        assert ctx_b.scope_key == "scope-B"
        assert ctx_a.session_id == "sess-scope-A"
        assert ctx_b.session_id == "sess-scope-B"
