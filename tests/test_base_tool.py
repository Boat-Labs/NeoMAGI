"""Tests for BaseTool changes in Phase 0 (RiskLevel + execute signature)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from src.tools.base import BaseTool, RiskLevel, ToolGroup, ToolMode

if TYPE_CHECKING:
    from src.tools.context import ToolContext


class _LowRiskTool(BaseTool):
    """Concrete tool for testing: explicitly low risk."""

    @property
    def name(self) -> str:
        return "test_low"

    @property
    def description(self) -> str:
        return "A low-risk test tool"

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
        return {"ok": True, "context_provided": context is not None}


class _HighRiskTool(BaseTool):
    """Concrete tool for testing: explicitly high risk."""

    @property
    def name(self) -> str:
        return "test_high"

    @property
    def description(self) -> str:
        return "A high-risk test tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.high

    async def execute(
        self, arguments: dict, context: ToolContext | None = None
    ) -> dict:
        return {"ok": True}


class _DefaultRiskTool(BaseTool):
    """Concrete tool that relies on default risk_level (should be high)."""

    @property
    def name(self) -> str:
        return "test_default"

    @property
    def description(self) -> str:
        return "Default risk test tool"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(
        self, arguments: dict, context: ToolContext | None = None
    ) -> dict:
        return {"ok": True}


class TestRiskLevel:
    def test_enum_values(self) -> None:
        assert RiskLevel.low == "low"
        assert RiskLevel.high == "high"

    def test_default_risk_level_is_high(self) -> None:
        tool = _DefaultRiskTool()
        assert tool.risk_level == RiskLevel.high

    def test_explicit_low_risk(self) -> None:
        tool = _LowRiskTool()
        assert tool.risk_level == RiskLevel.low

    def test_explicit_high_risk(self) -> None:
        tool = _HighRiskTool()
        assert tool.risk_level == RiskLevel.high


class TestExecuteSignature:
    @pytest.mark.asyncio
    async def test_execute_with_context_none(self) -> None:
        tool = _LowRiskTool()
        result = await tool.execute({"key": "val"})
        assert result["ok"] is True
        assert result["context_provided"] is False

    @pytest.mark.asyncio
    async def test_execute_with_context(self) -> None:
        from src.tools.context import ToolContext

        tool = _LowRiskTool()
        ctx = ToolContext(scope_key="main", session_id="s1")
        result = await tool.execute({"key": "val"}, ctx)
        assert result["ok"] is True
        assert result["context_provided"] is True
