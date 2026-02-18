"""Tests for F3: history message filtering for chat UI display."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from unittest.mock import patch

from src.session.manager import Message, SessionManager, _messages_to_history_format


class TestMessagesToHistoryFormat:
    """Unit tests for _messages_to_history_format."""

    def _make_msg(self, role, content="hello", **kwargs):
        return Message(role=role, content=content, **kwargs)

    def test_filters_system_and_tool_messages(self):
        messages = [
            self._make_msg("system", "You are an agent"),
            self._make_msg("user", "Hi"),
            self._make_msg("assistant", "Hello!"),
            self._make_msg("tool", '{"result": "ok"}', tool_call_id="call_1"),
            self._make_msg("assistant", "Done"),
        ]
        result = _messages_to_history_format(messages)
        assert len(result) == 3
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "Hello!"
        assert result[2]["role"] == "assistant"
        assert result[2]["content"] == "Done"

    def test_filters_empty_assistant_messages(self):
        messages = [
            self._make_msg("user", "Hi"),
            self._make_msg("assistant", "", tool_calls=[{"id": "call_1"}]),
            self._make_msg("assistant", "Real response"),
        ]
        result = _messages_to_history_format(messages)
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["content"] == "Real response"

    def test_output_keys_are_role_content_timestamp_only(self):
        messages = [
            self._make_msg("user", "Hi"),
            self._make_msg(
                "assistant", "Reply",
                tool_calls=[{"id": "x"}], tool_call_id="call_1",
            ),
        ]
        result = _messages_to_history_format(messages)
        for msg in result:
            assert set(msg.keys()) == {"role", "content", "timestamp"}

    def test_timestamp_is_iso_format(self):
        messages = [self._make_msg("user", "Hi")]
        result = _messages_to_history_format(messages)
        # Should be parseable as ISO format
        datetime.fromisoformat(result[0]["timestamp"])

    def test_empty_list(self):
        assert _messages_to_history_format([]) == []


class TestGetHistoryForDisplay:
    """Test SessionManager.get_history_for_display method."""

    @pytest.mark.asyncio
    async def test_nonexistent_session_returns_empty(self):
        manager = SessionManager(db_session_factory=MagicMock())
        # force=True in get_history_for_display triggers load_session_from_db;
        # patch to return False (session not found in DB).
        with patch.object(manager, "load_session_from_db", new_callable=AsyncMock, return_value=False):
            result = await manager.get_history_for_display("nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_filtered_history(self):
        manager = SessionManager(db_session_factory=MagicMock())
        # Patch _persist_message to avoid real DB calls
        with patch.object(manager, "_persist_message", new_callable=AsyncMock):
            await manager.append_message("s1", "system", "You are an agent")
            await manager.append_message("s1", "user", "Hi")
            await manager.append_message("s1", "assistant", "Hello!")
            await manager.append_message("s1", "tool", '{"ok": true}', tool_call_id="c1")

        # Patch load_session_from_db to no-op (messages already in memory).
        with patch.object(manager, "load_session_from_db", new_callable=AsyncMock, return_value=True):
            result = await manager.get_history_for_display("s1")
        assert len(result) == 2
        assert result[0]["role"] == "user"
        assert result[1]["role"] == "assistant"


class TestHistoryContract:
    """R6c: history contract — duplicate content, role filtering, empty session."""

    def _make_msg(self, role, content="hello", **kwargs):
        return Message(role=role, content=content, **kwargs)

    def test_consecutive_same_content_not_swallowed(self):
        """User sends two identical messages — both appear in history."""
        messages = [
            self._make_msg("user", "ping"),
            self._make_msg("assistant", "pong"),
            self._make_msg("user", "ping"),
            self._make_msg("assistant", "pong"),
        ]
        result = _messages_to_history_format(messages)
        assert len(result) == 4

    def test_only_user_and_assistant(self):
        """system and tool messages are excluded."""
        messages = [
            self._make_msg("system", "sys prompt"),
            self._make_msg("user", "hi"),
            self._make_msg("tool", '{"ok":true}', tool_call_id="c1"),
            self._make_msg("assistant", "hello"),
        ]
        result = _messages_to_history_format(messages)
        roles = [m["role"] for m in result]
        assert set(roles) <= {"user", "assistant"}
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_empty_session_returns_empty_list(self):
        manager = SessionManager(db_session_factory=MagicMock())
        with patch.object(manager, "load_session_from_db", new_callable=AsyncMock, return_value=False):
            result = await manager.get_history_for_display("nonexistent-xyz")
        assert result == []


class TestGatewayHistoryHandler:
    """Integration test: chat.history handler calls get_history_for_display."""

    @pytest.mark.asyncio
    async def test_handler_calls_display_method(self):
        from src.gateway.app import _handle_chat_history

        mock_ws = MagicMock()
        mock_ws.send_text = AsyncMock()

        mock_manager = MagicMock()
        mock_manager.get_history_for_display = AsyncMock(return_value=[])
        mock_ws.app.state.session_manager = mock_manager

        await _handle_chat_history(mock_ws, "req-1", {"session_id": "test"})

        mock_manager.get_history_for_display.assert_called_once_with("test")
