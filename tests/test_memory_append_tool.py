"""Tests for MemoryAppendTool."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from src.config.settings import MemorySettings
from src.memory.writer import MemoryWriter
from src.tools.base import RiskLevel, ToolGroup, ToolMode
from src.tools.builtins.memory_append import MemoryAppendTool
from src.tools.context import ToolContext


def _make_tool(tmp_path: Path) -> MemoryAppendTool:
    settings = MemorySettings(
        workspace_path=tmp_path,
        max_daily_note_bytes=32_768,
        daily_notes_load_days=2,
        daily_notes_max_tokens=4000,
        flush_min_confidence=0.5,
    )
    writer = MemoryWriter(tmp_path, settings)
    return MemoryAppendTool(writer)


class TestMemoryAppendToolProperties:
    def test_name(self, tmp_path: Path) -> None:
        tool = _make_tool(tmp_path)
        assert tool.name == "memory_append"

    def test_group(self, tmp_path: Path) -> None:
        tool = _make_tool(tmp_path)
        assert tool.group == ToolGroup.memory

    def test_allowed_modes(self, tmp_path: Path) -> None:
        tool = _make_tool(tmp_path)
        assert ToolMode.chat_safe in tool.allowed_modes
        assert ToolMode.coding in tool.allowed_modes

    def test_risk_level_high(self, tmp_path: Path) -> None:
        tool = _make_tool(tmp_path)
        assert tool.risk_level == RiskLevel.high

    def test_parameters_require_text(self, tmp_path: Path) -> None:
        tool = _make_tool(tmp_path)
        params = tool.parameters
        assert "text" in params["properties"]
        assert "text" in params["required"]


class TestMemoryAppendToolExecute:
    @pytest.mark.asyncio
    async def test_normal_write(self, tmp_path: Path) -> None:
        tool = _make_tool(tmp_path)
        ctx = ToolContext(scope_key="main", session_id="s1")

        result = await tool.execute({"text": "Remember this"}, ctx)

        assert result["ok"] is True
        path = tmp_path / "memory" / f"{date.today().isoformat()}.md"
        content = path.read_text(encoding="utf-8")
        assert "Remember this" in content

    @pytest.mark.asyncio
    async def test_empty_text_rejected(self, tmp_path: Path) -> None:
        tool = _make_tool(tmp_path)
        ctx = ToolContext(scope_key="main", session_id="s1")

        result = await tool.execute({"text": ""}, ctx)
        assert result["error_code"] == "INVALID_ARGS"

    @pytest.mark.asyncio
    async def test_whitespace_text_rejected(self, tmp_path: Path) -> None:
        tool = _make_tool(tmp_path)
        ctx = ToolContext(scope_key="main", session_id="s1")

        result = await tool.execute({"text": "   "}, ctx)
        assert result["error_code"] == "INVALID_ARGS"

    @pytest.mark.asyncio
    async def test_scope_key_from_context(self, tmp_path: Path) -> None:
        tool = _make_tool(tmp_path)
        ctx = ToolContext(scope_key="main", session_id="s1")

        await tool.execute({"text": "scoped note"}, ctx)

        path = tmp_path / "memory" / f"{date.today().isoformat()}.md"
        content = path.read_text(encoding="utf-8")
        assert "scope: main" in content

    @pytest.mark.asyncio
    async def test_no_context_defaults_to_main(self, tmp_path: Path) -> None:
        tool = _make_tool(tmp_path)

        result = await tool.execute({"text": "no context"}, None)

        assert result["ok"] is True
        path = tmp_path / "memory" / f"{date.today().isoformat()}.md"
        content = path.read_text(encoding="utf-8")
        assert "scope: main" in content
