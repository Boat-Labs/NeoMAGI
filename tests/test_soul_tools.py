"""Tests for soul_propose, soul_status, soul_rollback tools."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.memory.evolution import EvalResult, SoulVersion
from src.tools.base import RiskLevel, ToolGroup, ToolMode
from src.tools.builtins.soul_propose import SoulProposeTool
from src.tools.builtins.soul_rollback import SoulRollbackTool
from src.tools.builtins.soul_status import SoulStatusTool
from src.tools.context import ToolContext


class TestSoulProposeToolProperties:
    def test_name(self) -> None:
        assert SoulProposeTool().name == "soul_propose"

    def test_group(self) -> None:
        assert SoulProposeTool().group == ToolGroup.memory

    def test_risk_high(self) -> None:
        assert SoulProposeTool().risk_level == RiskLevel.high

    def test_modes(self) -> None:
        tool = SoulProposeTool()
        assert ToolMode.chat_safe in tool.allowed_modes
        assert ToolMode.coding in tool.allowed_modes


class TestSoulProposeToolExecute:
    @pytest.mark.asyncio
    async def test_no_engine_returns_error(self) -> None:
        tool = SoulProposeTool(engine=None)
        result = await tool.execute({"intent": "test", "new_content": "x"}, None)
        assert result["error_code"] == "NOT_CONFIGURED"

    @pytest.mark.asyncio
    async def test_missing_args(self) -> None:
        engine = MagicMock()
        tool = SoulProposeTool(engine=engine)
        result = await tool.execute({"intent": ""}, None)
        assert result["error_code"] == "INVALID_ARGS"

    @pytest.mark.asyncio
    async def test_propose_eval_pass_applies(self) -> None:
        engine = MagicMock()
        engine.propose = AsyncMock(return_value=1)
        engine.evaluate = AsyncMock(
            return_value=EvalResult(passed=True, summary="All checks passed")
        )
        engine.apply = AsyncMock()

        tool = SoulProposeTool(engine=engine)
        ctx = ToolContext(scope_key="main", session_id="s1")
        result = await tool.execute(
            {"intent": "Update", "new_content": "# New Soul"},
            ctx,
        )

        assert result["status"] == "applied"
        assert result["version"] == 1
        engine.apply.assert_called_once_with(1)

    @pytest.mark.asyncio
    async def test_propose_eval_fail_rejects(self) -> None:
        engine = MagicMock()
        engine.propose = AsyncMock(return_value=1)
        engine.evaluate = AsyncMock(
            return_value=EvalResult(passed=False, summary="Failed: size_limit")
        )

        tool = SoulProposeTool(engine=engine)
        result = await tool.execute(
            {"intent": "Update", "new_content": "# New"},
            None,
        )

        assert result["status"] == "rejected"
        assert "size_limit" in result["eval"]


class TestSoulStatusToolProperties:
    def test_name(self) -> None:
        assert SoulStatusTool().name == "soul_status"

    def test_risk_low(self) -> None:
        assert SoulStatusTool().risk_level == RiskLevel.low


class TestSoulStatusToolExecute:
    @pytest.mark.asyncio
    async def test_no_engine(self) -> None:
        tool = SoulStatusTool(engine=None)
        result = await tool.execute({}, None)
        assert result["error_code"] == "NOT_CONFIGURED"

    @pytest.mark.asyncio
    async def test_no_active_version(self) -> None:
        engine = MagicMock()
        engine.get_current_version = AsyncMock(return_value=None)

        tool = SoulStatusTool(engine=engine)
        result = await tool.execute({}, None)
        assert result["has_active_version"] is False

    @pytest.mark.asyncio
    async def test_with_active_version(self) -> None:
        engine = MagicMock()
        engine.get_current_version = AsyncMock(
            return_value=SoulVersion(
                id=1, version=2, content="# Soul", status="active",
                proposal=None, eval_result=None, created_by="agent",
                created_at=None,
            )
        )

        tool = SoulStatusTool(engine=engine)
        result = await tool.execute({}, None)
        assert result["has_active_version"] is True
        assert result["current"]["version"] == 2

    @pytest.mark.asyncio
    async def test_with_history(self) -> None:
        engine = MagicMock()
        engine.get_current_version = AsyncMock(return_value=None)
        engine.get_audit_trail = AsyncMock(return_value=[
            SoulVersion(
                id=1, version=1, content="", status="superseded",
                proposal=None, eval_result=None, created_by="agent",
                created_at=None,
            ),
        ])

        tool = SoulStatusTool(engine=engine)
        result = await tool.execute({"include_history": True, "limit": 3}, None)
        assert "history" in result
        assert len(result["history"]) == 1


class TestSoulRollbackToolProperties:
    def test_name(self) -> None:
        assert SoulRollbackTool().name == "soul_rollback"

    def test_risk_high(self) -> None:
        assert SoulRollbackTool().risk_level == RiskLevel.high


class TestSoulRollbackToolExecute:
    @pytest.mark.asyncio
    async def test_no_engine(self) -> None:
        tool = SoulRollbackTool(engine=None)
        result = await tool.execute({"action": "rollback"}, None)
        assert result["error_code"] == "NOT_CONFIGURED"

    @pytest.mark.asyncio
    async def test_invalid_action(self) -> None:
        engine = MagicMock()
        tool = SoulRollbackTool(engine=engine)
        result = await tool.execute({"action": "invalid"}, None)
        assert result["error_code"] == "INVALID_ARGS"

    @pytest.mark.asyncio
    async def test_rollback_success(self) -> None:
        engine = MagicMock()
        engine.rollback = AsyncMock(return_value=3)

        tool = SoulRollbackTool(engine=engine)
        result = await tool.execute({"action": "rollback"}, None)
        assert result["status"] == "rolled_back"
        assert result["new_active_version"] == 3

    @pytest.mark.asyncio
    async def test_veto_requires_version(self) -> None:
        engine = MagicMock()
        tool = SoulRollbackTool(engine=engine)
        result = await tool.execute({"action": "veto"}, None)
        assert result["error_code"] == "INVALID_ARGS"

    @pytest.mark.asyncio
    async def test_veto_success(self) -> None:
        engine = MagicMock()
        engine.veto = AsyncMock()

        tool = SoulRollbackTool(engine=engine)
        result = await tool.execute({"action": "veto", "version": 2}, None)
        assert result["status"] == "vetoed"
        assert result["version"] == 2
