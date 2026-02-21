"""Tests for F2: tool call args parsing with dict type enforcement."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.agent import AgentLoop, _safe_parse_args
from src.agent.events import ToolCallInfo
from src.agent.model_client import ContentDelta, ToolCallsComplete
from src.tools.base import ToolMode


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
        # Mock chat_stream_with_tools: first call returns tool_call with bad JSON,
        # second call returns final text
        async def stream_with_tools_iter(*args, **kwargs):
            yield ToolCallsComplete(
                tool_calls=[{"id": "call_1", "name": "test_tool", "arguments": "{bad json}"}]
            )

        async def stream_final_iter(*args, **kwargs):
            yield ContentDelta(text="Done")

        model_client = MagicMock()
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[stream_with_tools_iter(), stream_final_iter()]
        )

        session_manager = MagicMock()
        user_msg = MagicMock()
        user_msg.seq = 0
        session_manager.append_message = AsyncMock(return_value=user_msg)
        session_manager.get_mode = AsyncMock(return_value=ToolMode.chat_safe)
        session_manager.get_compaction_state = AsyncMock(return_value=None)
        session_manager.get_effective_history = MagicMock(return_value=[])
        session_manager.get_history_with_seq = MagicMock(return_value=[])

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

        async def stream_with_tools_iter(*args, **kwargs):
            yield ToolCallsComplete(
                tool_calls=[{"id": "call_2", "name": "test_tool", "arguments": "null"}]
            )

        async def stream_final_iter(*args, **kwargs):
            yield ContentDelta(text="Done")

        model_client = MagicMock()
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[stream_with_tools_iter(), stream_final_iter()]
        )

        session_manager = MagicMock()
        user_msg = MagicMock()
        user_msg.seq = 0
        session_manager.append_message = AsyncMock(return_value=user_msg)
        session_manager.get_mode = AsyncMock(return_value=ToolMode.chat_safe)
        session_manager.get_compaction_state = AsyncMock(return_value=None)
        session_manager.get_effective_history = MagicMock(return_value=[])
        session_manager.get_history_with_seq = MagicMock(return_value=[])

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
