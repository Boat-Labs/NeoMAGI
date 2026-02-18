"""Tests for F2: tool call args parsing with dict type enforcement."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.agent import AgentLoop, _safe_parse_args
from src.agent.events import ToolCallInfo


class TestSafeParseArgs:
    """Unit tests for _safe_parse_args."""

    def test_valid_dict(self):
        result, err = _safe_parse_args('{"a": 1}')
        assert result == {"a": 1}
        assert err is None

    def test_empty_dict(self):
        result, err = _safe_parse_args("{}")
        assert result == {}
        assert err is None

    def test_invalid_json(self):
        result, err = _safe_parse_args("{bad}")
        assert result == {}
        assert err is not None
        assert "JSON parse error" in err

    def test_list_rejected(self):
        result, err = _safe_parse_args("[]")
        assert result == {}
        assert "Expected dict, got list" in err

    def test_string_rejected(self):
        result, err = _safe_parse_args('"hello"')
        assert result == {}
        assert "Expected dict, got str" in err

    def test_int_rejected(self):
        result, err = _safe_parse_args("123")
        assert result == {}
        assert "Expected dict, got int" in err

    def test_none_arguments(self):
        result, err = _safe_parse_args(None)
        assert result == {}
        assert err is not None
        assert "JSON parse error" in err


class TestExecuteToolDictValidation:
    """Test _execute_tool rejects non-dict JSON with INVALID_ARGS."""

    @pytest.fixture()
    def agent_loop(self):
        tool = MagicMock()
        tool.execute = AsyncMock(return_value={"ok": True})

        registry = MagicMock()
        registry.get.return_value = tool
        registry.list_tools.return_value = [tool]
        registry.get_tools_schema.return_value = []

        return AgentLoop(
            model_client=MagicMock(),
            session_manager=MagicMock(),
            workspace_dir=MagicMock(),
            tool_registry=registry,
        )

    @pytest.mark.asyncio
    async def test_int_returns_invalid_args(self, agent_loop):
        result = await agent_loop._execute_tool("some_tool", "123")
        assert result["error_code"] == "INVALID_ARGS"

    @pytest.mark.asyncio
    async def test_list_returns_invalid_args(self, agent_loop):
        result = await agent_loop._execute_tool("some_tool", "[]")
        assert result["error_code"] == "INVALID_ARGS"

    @pytest.mark.asyncio
    async def test_bad_json_returns_invalid_args(self, agent_loop):
        result = await agent_loop._execute_tool("some_tool", "{bad}")
        assert result["error_code"] == "INVALID_ARGS"


class TestHandleMessageWithBadArgs:
    """Test that handle_message doesn't crash on malformed tool call args."""

    @pytest.mark.asyncio
    async def test_bad_args_yields_tool_call_with_empty_dict(self, tmp_path):
        # Mock the LLM to return a tool_call with bad JSON, then a text response
        bad_tool_call = MagicMock()
        bad_tool_call.id = "call_1"
        bad_tool_call.function.name = "test_tool"
        bad_tool_call.function.arguments = "{bad json}"

        response_with_tools = MagicMock()
        response_with_tools.tool_calls = [bad_tool_call]
        response_with_tools.content = ""

        response_final = MagicMock()
        response_final.tool_calls = None
        response_final.content = "Done"

        model_client = MagicMock()
        model_client.chat_completion = AsyncMock(
            side_effect=[response_with_tools, response_final]
        )

        session_manager = MagicMock()
        session_manager.get_or_create.return_value = MagicMock(messages=[])
        session_manager.get_history.return_value = []

        registry = MagicMock()
        registry.list_tools.return_value = []
        registry.get_tools_schema.return_value = []
        registry.get.return_value = None  # tool not found → won't execute

        agent = AgentLoop(
            model_client=model_client,
            session_manager=session_manager,
            workspace_dir=tmp_path,
            tool_registry=registry,
        )

        events = []
        async for event in agent.handle_message("test", "hi"):
            events.append(event)

        # Should have ToolCallInfo with empty dict args (not crash)
        tool_events = [e for e in events if isinstance(e, ToolCallInfo)]
        assert len(tool_events) == 1
        assert tool_events[0].arguments == {}

    @pytest.mark.asyncio
    async def test_none_args_yields_tool_call_with_empty_dict(self, tmp_path):
        """Ollama/Gemini may return null for tool call arguments."""
        none_tool_call = MagicMock()
        none_tool_call.id = "call_2"
        none_tool_call.function.name = "test_tool"
        none_tool_call.function.arguments = None

        response_with_tools = MagicMock()
        response_with_tools.tool_calls = [none_tool_call]
        response_with_tools.content = ""

        response_final = MagicMock()
        response_final.tool_calls = None
        response_final.content = "Done"

        model_client = MagicMock()
        model_client.chat_completion = AsyncMock(
            side_effect=[response_with_tools, response_final]
        )

        session_manager = MagicMock()
        session_manager.get_or_create.return_value = MagicMock(messages=[])
        session_manager.get_history.return_value = []

        registry = MagicMock()
        registry.list_tools.return_value = []
        registry.get_tools_schema.return_value = []
        registry.get.return_value = None

        agent = AgentLoop(
            model_client=model_client,
            session_manager=session_manager,
            workspace_dir=tmp_path,
            tool_registry=registry,
        )

        events = []
        async for event in agent.handle_message("test", "hi"):
            events.append(event)

        tool_events = [e for e in events if isinstance(e, ToolCallInfo)]
        assert len(tool_events) == 1
        assert tool_events[0].arguments == {}
