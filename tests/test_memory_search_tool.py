"""Tests for MemorySearchTool (upgraded from placeholder)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.memory.searcher import MemorySearchResult
from src.tools.base import RiskLevel, ToolGroup, ToolMode
from src.tools.builtins.memory_search import MemorySearchTool
from src.tools.context import ToolContext


class TestMemorySearchToolProperties:
    def test_name(self) -> None:
        tool = MemorySearchTool()
        assert tool.name == "memory_search"

    def test_group(self) -> None:
        tool = MemorySearchTool()
        assert tool.group == ToolGroup.memory

    def test_allowed_modes(self) -> None:
        tool = MemorySearchTool()
        assert ToolMode.chat_safe in tool.allowed_modes
        assert ToolMode.coding in tool.allowed_modes

    def test_risk_level_low(self) -> None:
        tool = MemorySearchTool()
        assert tool.risk_level == RiskLevel.low

    def test_parameters_require_query(self) -> None:
        tool = MemorySearchTool()
        params = tool.parameters
        assert "query" in params["properties"]
        assert "query" in params["required"]


class TestMemorySearchToolExecute:
    @pytest.mark.asyncio
    async def test_no_searcher_returns_not_configured(self) -> None:
        tool = MemorySearchTool(searcher=None)
        ctx = ToolContext(scope_key="main", session_id="s1")

        result = await tool.execute({"query": "test"}, ctx)
        assert result["message"] == "Memory search not yet configured"
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_empty_query_rejected(self) -> None:
        searcher = MagicMock()
        tool = MemorySearchTool(searcher=searcher)
        ctx = ToolContext(scope_key="main", session_id="s1")

        result = await tool.execute({"query": ""}, ctx)
        assert result["error_code"] == "INVALID_ARGS"

    @pytest.mark.asyncio
    async def test_normal_search(self) -> None:
        mock_result = MemorySearchResult(
            entry_id=1,
            scope_key="main",
            source_type="daily_note",
            source_path="memory/2026-02-22.md",
            title="",
            content="User prefers dark mode",
            score=0.8,
            tags=["user_preference"],
            created_at=datetime.now(UTC),
        )
        searcher = MagicMock()
        searcher.search = AsyncMock(return_value=[mock_result])

        tool = MemorySearchTool(searcher=searcher)
        ctx = ToolContext(scope_key="main", session_id="s1")

        result = await tool.execute({"query": "dark mode"}, ctx)

        assert result["total"] == 1
        assert result["results"][0]["content"] == "User prefers dark mode"
        assert result["results"][0]["score"] == 0.8
        searcher.search.assert_called_once_with(
            query="dark mode", scope_key="main", limit=10
        )

    @pytest.mark.asyncio
    async def test_scope_key_from_context(self) -> None:
        searcher = MagicMock()
        searcher.search = AsyncMock(return_value=[])

        tool = MemorySearchTool(searcher=searcher)
        ctx = ToolContext(scope_key="main", session_id="s1")

        await tool.execute({"query": "test"}, ctx)

        searcher.search.assert_called_once_with(
            query="test", scope_key="main", limit=10
        )

    @pytest.mark.asyncio
    async def test_content_truncation(self) -> None:
        long_content = "A" * 1000
        mock_result = MemorySearchResult(
            entry_id=1,
            scope_key="main",
            source_type="daily_note",
            source_path=None,
            title="",
            content=long_content,
            score=0.5,
            tags=[],
            created_at=datetime.now(UTC),
        )
        searcher = MagicMock()
        searcher.search = AsyncMock(return_value=[mock_result])

        tool = MemorySearchTool(searcher=searcher)
        ctx = ToolContext(scope_key="main", session_id="s1")

        result = await tool.execute({"query": "test"}, ctx)
        assert len(result["results"][0]["content"]) == 500

    @pytest.mark.asyncio
    async def test_custom_limit(self) -> None:
        searcher = MagicMock()
        searcher.search = AsyncMock(return_value=[])

        tool = MemorySearchTool(searcher=searcher)
        ctx = ToolContext(scope_key="main", session_id="s1")

        await tool.execute({"query": "test", "limit": 5}, ctx)

        searcher.search.assert_called_once_with(
            query="test", scope_key="main", limit=5
        )

    @pytest.mark.asyncio
    async def test_no_context_defaults_to_main(self) -> None:
        searcher = MagicMock()
        searcher.search = AsyncMock(return_value=[])

        tool = MemorySearchTool(searcher=searcher)

        await tool.execute({"query": "test"}, None)

        searcher.search.assert_called_once_with(
            query="test", scope_key="main", limit=10
        )
