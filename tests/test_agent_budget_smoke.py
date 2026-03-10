"""Smoke tests for agent loop budget check integration (Phase 1/3).

Verifies that budget_check log is emitted with all required fields
during agent loop execution. Uses mock model client.

Updated for Phase 3: agent loop now uses get_effective_history + get_compaction_state.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.agent import AgentLoop
from src.agent.model_client import ContentDelta
from src.config.settings import CompactionSettings
from src.session.manager import Message, MessageWithSeq
from src.tools.base import ToolMode


def _make_stream_response(text: str = "Hello!"):
    """Create a mock async iterator that yields a ContentDelta."""

    async def stream(*args, **kwargs):
        yield ContentDelta(text=text)

    return stream


def _make_session_manager(history_msgs: list[MessageWithSeq] | None = None):
    """Create a mock SessionManager with Phase 3 interface."""
    sm = MagicMock()
    user_msg = MagicMock(spec=Message)
    user_msg.seq = 0
    sm.append_message = AsyncMock(return_value=user_msg)
    sm.get_mode = AsyncMock(return_value=ToolMode.chat_safe)
    sm.get_compaction_state = AsyncMock(return_value=None)
    sm.get_effective_history = MagicMock(return_value=history_msgs or [])
    sm.get_history_with_seq = MagicMock(return_value=history_msgs or [])
    return sm


_BUDGET_CHECK_REQUIRED_FIELDS = [
    "session_id", "model", "iteration", "current_tokens",
    "status", "usable_budget", "warn_threshold", "compact_threshold", "tokenizer_mode",
]


def _assert_budget_check_fields(call_kwargs: dict) -> None:
    """Assert all required budget_check fields are present."""
    for field in _BUDGET_CHECK_REQUIRED_FIELDS:
        assert field in call_kwargs, f"Missing field: {field}"


@pytest.mark.asyncio
class TestAgentBudgetSmoke:
    """Verify budget_check log is emitted with correct fields."""

    async def test_budget_check_log_emitted(self, tmp_path):
        model_client = MagicMock()
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        history = [
            MessageWithSeq(
                seq=0, role="user", content="Hi there",
                tool_calls=None, tool_call_id=None,
            ),
        ]
        session_manager = _make_session_manager(history)

        settings = CompactionSettings(
            context_limit=10_000, warn_ratio=0.80, compact_ratio=0.90,
            reserved_output_tokens=1000, safety_margin_tokens=500,
        )

        agent = AgentLoop(
            model_client=model_client, session_manager=session_manager,
            workspace_dir=tmp_path, compaction_settings=settings,
        )

        with patch("src.agent.agent.logger") as mock_logger:
            async for _ in agent.handle_message("test-session", "Hi"):
                pass

            budget_calls = [
                call for call in mock_logger.info.call_args_list
                if call.args and call.args[0] == "budget_check"
            ]
            assert len(budget_calls) >= 1
            _assert_budget_check_fields(budget_calls[0].kwargs)

    async def test_budget_check_tokenizer_mode_exact(self, tmp_path):
        """Verify tokenizer_mode=exact for known OpenAI model."""
        model_client = MagicMock()
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        session_manager = _make_session_manager()

        settings = CompactionSettings(
            context_limit=10_000,
            warn_ratio=0.80,
            compact_ratio=0.90,
            reserved_output_tokens=1000,
            safety_margin_tokens=500,
        )

        agent = AgentLoop(
            model_client=model_client,
            session_manager=session_manager,
            workspace_dir=tmp_path,
            model="gpt-4o-mini",
            compaction_settings=settings,
        )

        with patch("src.agent.agent.logger") as mock_logger:
            async for _ in agent.handle_message("test-session", "Hi"):
                pass

            budget_calls = [
                call
                for call in mock_logger.info.call_args_list
                if call.args and call.args[0] == "budget_check"
            ]
            assert budget_calls[0].kwargs["tokenizer_mode"] == "exact"

    async def test_budget_check_tokenizer_mode_estimate(self, tmp_path):
        """Verify tokenizer_mode=estimate for unknown model."""
        model_client = MagicMock()
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        session_manager = _make_session_manager()

        settings = CompactionSettings(
            context_limit=10_000,
            warn_ratio=0.80,
            compact_ratio=0.90,
            reserved_output_tokens=1000,
            safety_margin_tokens=500,
        )

        agent = AgentLoop(
            model_client=model_client,
            session_manager=session_manager,
            workspace_dir=tmp_path,
            model="unknown-model-xyz",
            compaction_settings=settings,
        )

        with patch("src.agent.agent.logger") as mock_logger:
            async for _ in agent.handle_message("test-session", "Hi"):
                pass

            budget_calls = [
                call
                for call in mock_logger.info.call_args_list
                if call.args and call.args[0] == "budget_check"
            ]
            assert budget_calls[0].kwargs["tokenizer_mode"] == "estimate"

    async def test_no_budget_check_without_settings(self, tmp_path):
        """When compaction_settings is not provided, no budget_check log."""
        model_client = MagicMock()
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        session_manager = _make_session_manager()

        agent = AgentLoop(
            model_client=model_client,
            session_manager=session_manager,
            workspace_dir=tmp_path,
        )

        with patch("src.agent.agent.logger") as mock_logger:
            async for _ in agent.handle_message("test-session", "Hi"):
                pass

            budget_calls = [
                call
                for call in mock_logger.info.call_args_list
                if call.args and call.args[0] == "budget_check"
            ]
            assert len(budget_calls) == 0

    async def test_budget_status_values(self, tmp_path):
        """Verify computed budget values are reasonable."""
        model_client = MagicMock()
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        history = [
            MessageWithSeq(
                seq=0, role="user", content="Short message",
                tool_calls=None, tool_call_id=None,
            ),
        ]
        session_manager = _make_session_manager(history)

        settings = CompactionSettings(
            context_limit=10_000,
            warn_ratio=0.80,
            compact_ratio=0.90,
            reserved_output_tokens=1000,
            safety_margin_tokens=500,
        )

        agent = AgentLoop(
            model_client=model_client,
            session_manager=session_manager,
            workspace_dir=tmp_path,
            compaction_settings=settings,
        )

        with patch("src.agent.agent.logger") as mock_logger:
            async for _ in agent.handle_message("test-session", "Test"):
                pass

            budget_calls = [
                call
                for call in mock_logger.info.call_args_list
                if call.args and call.args[0] == "budget_check"
            ]
            kw = budget_calls[0].kwargs

            # usable = 10000 - 1000 - 500 = 8500
            assert kw["usable_budget"] == 8500
            assert kw["warn_threshold"] == 6800  # 8500 * 0.80
            assert kw["compact_threshold"] == 7650  # 8500 * 0.90
            assert kw["current_tokens"] > 0
            assert kw["status"] == "ok"  # small message, should be ok
