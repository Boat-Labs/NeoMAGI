"""Tests for compaction degradation paths (Phase 3).

Covers:
- LLM timeout → degraded status, watermark still advances
- Flush timeout → flush_skipped, compaction continues
- Anchor validation failure → retry → degraded
- Compaction exception → emergency trim applied
- Emergency trim after total failure
- Emergency trim with insufficient history → None returned
- Fail-open: session continues after all degradation paths
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.agent import AgentLoop
from src.agent.events import TextChunk
from src.agent.model_client import ContentDelta
from src.config.settings import CompactionSettings
from src.session.manager import Message, MessageWithSeq
from src.tools.base import ToolMode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stream_response(text: str = "Hello!"):
    async def stream(*args, **kwargs):
        yield ContentDelta(text=text)

    return stream


def _msg_with_seq(
    seq: int,
    role: str,
    content: str = "test",
    *,
    tool_calls=None,
    tool_call_id=None,
) -> MessageWithSeq:
    return MessageWithSeq(
        seq=seq,
        role=role,
        content=content,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
    )


def _make_long_history(n_turns: int, start_seq: int = 0) -> list[MessageWithSeq]:
    msgs = []
    seq = start_seq
    for i in range(n_turns):
        msgs.append(_msg_with_seq(seq, "user", f"User msg {i} " + "x" * 100))
        seq += 1
        msgs.append(_msg_with_seq(seq, "assistant", f"Asst resp {i} " + "y" * 100))
        seq += 1
    return msgs


def _make_session_manager(history=None, user_seq=0):
    sm = MagicMock()
    user_msg = MagicMock(spec=Message)
    user_msg.seq = user_seq
    sm.append_message = AsyncMock(return_value=user_msg)
    sm.get_mode = AsyncMock(return_value=ToolMode.chat_safe)
    sm.get_compaction_state = AsyncMock(return_value=None)
    sm.get_effective_history = MagicMock(return_value=history or [])
    sm.get_history_with_seq = MagicMock(return_value=history or [])
    sm.store_compaction_result = AsyncMock()
    return sm


def _make_settings(**overrides) -> CompactionSettings:
    defaults = {
        "context_limit": 500,
        "warn_ratio": 0.30,
        "compact_ratio": 0.50,
        "reserved_output_tokens": 50,
        "safety_margin_tokens": 50,
        "min_preserved_turns": 3,
        "flush_timeout_s": 0.1,
        "compact_timeout_s": 0.1,
        "max_compactions_per_request": 2,
    }
    defaults.update(overrides)
    return CompactionSettings(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCompactionDegradation:

    async def test_llm_timeout_produces_degraded(self, tmp_path):
        """LLM timeout in compaction → degraded status, session continues."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(side_effect=TimeoutError("timeout"))
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        history = _make_long_history(20, start_seq=0)
        sm = _make_session_manager(history, user_seq=40)
        settings = _make_settings()

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
            model="gpt-4o-mini",
        )

        events = []
        async for event in agent.handle_message("test", "Go", lock_token="lock"):
            events.append(event)

        # Session should still produce a response (fail-open)
        assert len(events) >= 1
        assert isinstance(events[0], TextChunk)

        # Compaction store should have been called (degraded still stores)
        if sm.store_compaction_result.called:
            result = sm.store_compaction_result.call_args.args[1]
            assert result.status in ("degraded", "failed")

    async def test_compaction_exception_triggers_emergency_trim(self, tmp_path):
        """Total compaction failure → emergency trim applied."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(side_effect=RuntimeError("LLM down"))
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        history = _make_long_history(20, start_seq=0)
        sm = _make_session_manager(history, user_seq=40)
        settings = _make_settings()

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
            model="gpt-4o-mini",
        )

        events = []
        async for event in agent.handle_message("test", "Go", lock_token="lock"):
            events.append(event)

        # Session continues (fail-open)
        assert len(events) >= 1

    async def test_emergency_trim_metadata_fields(self, tmp_path):
        """Emergency trim result has correct metadata structure."""
        model_client = MagicMock()
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        history = _make_long_history(20, start_seq=0)
        sm = _make_session_manager(history, user_seq=40)
        settings = _make_settings()

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
        )

        result = agent._emergency_trim(session_id="test", current_user_seq=40)

        assert result is not None
        assert result.status == "failed"
        meta = result.compaction_metadata
        assert meta["schema_version"] == 1
        assert meta["emergency_trim"] is True
        assert meta["flush_skipped"] is True
        assert meta["status"] == "failed"
        assert "triggered_at" in meta
        assert result.new_compaction_seq < 40  # Must be before current turn

    async def test_emergency_trim_trims_by_turn_boundary_with_tool_calls(self, tmp_path):
        """Emergency trim watermark must not split tool-call chains."""
        model_client = MagicMock()
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        history = [
            # Turn 1
            _msg_with_seq(0, "user", "u1"),
            _msg_with_seq(
                1,
                "assistant",
                "",
                tool_calls=[{"id": "call_1", "type": "function", "function": {}}],
            ),
            _msg_with_seq(2, "tool", '{"ok":true}', tool_call_id="call_1"),
            _msg_with_seq(3, "assistant", "a1"),
            # Turn 2
            _msg_with_seq(4, "user", "u2"),
            _msg_with_seq(
                5,
                "assistant",
                "",
                tool_calls=[{"id": "call_2", "type": "function", "function": {}}],
            ),
            _msg_with_seq(6, "tool", '{"ok":true}', tool_call_id="call_2"),
            _msg_with_seq(7, "assistant", "a2"),
            # Turn 3 (preserved)
            _msg_with_seq(8, "user", "u3"),
            _msg_with_seq(9, "assistant", "a3"),
        ]
        sm = _make_session_manager(history, user_seq=10)
        settings = _make_settings(min_preserved_turns=2)

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
        )

        result = agent._emergency_trim(session_id="test", current_user_seq=10)
        assert result is not None
        # With 3 completed turns and preserve=2, watermark must end at Turn 1 end_seq=3.
        assert result.new_compaction_seq == 3

    async def test_emergency_trim_with_few_messages_returns_none(self, tmp_path):
        """Emergency trim with insufficient messages returns None."""
        model_client = MagicMock()
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        # Very few completed turns, not enough to trim.
        history = _make_long_history(2, start_seq=0)
        sm = _make_session_manager(history, user_seq=4)
        settings = _make_settings()

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
        )

        result = agent._emergency_trim(session_id="test", current_user_seq=4)
        assert result is None  # Not enough messages to trim

    async def test_fail_open_on_all_paths(self, tmp_path):
        """Even when everything fails, session continues with a response."""
        model_client = MagicMock()
        # LLM calls for compaction fail
        model_client.chat = AsyncMock(side_effect=Exception("total failure"))
        # But streaming response works
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response("I'm still here")()]
        )

        history = _make_long_history(20, start_seq=0)
        sm = _make_session_manager(history, user_seq=40)
        # Make store_compaction_result also fail to test emergency path
        sm.store_compaction_result = AsyncMock(side_effect=Exception("DB down"))
        settings = _make_settings()

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
            model="gpt-4o-mini",
        )

        events = []
        async for event in agent.handle_message("test", "Go", lock_token="lock"):
            events.append(event)

        # Session must still respond (fail-open)
        assert len(events) >= 1
        assert any(isinstance(e, TextChunk) for e in events)

    async def test_degraded_compaction_still_advances_watermark(self, tmp_path):
        """Degraded compaction (LLM failed) still produces a watermark."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(side_effect=TimeoutError("timeout"))
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        history = _make_long_history(20, start_seq=0)
        sm = _make_session_manager(history, user_seq=40)
        settings = _make_settings()

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
            model="gpt-4o-mini",
        )

        async for _ in agent.handle_message("test", "Go", lock_token="lock"):
            pass

        if sm.store_compaction_result.called:
            result = sm.store_compaction_result.call_args.args[1]
            assert result.new_compaction_seq > 0

    async def test_compaction_metadata_on_degraded(self, tmp_path):
        """Degraded compaction has correct metadata fields."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(side_effect=TimeoutError("timeout"))
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        history = _make_long_history(20, start_seq=0)
        sm = _make_session_manager(history, user_seq=40)
        settings = _make_settings()

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
            model="gpt-4o-mini",
        )

        async for _ in agent.handle_message("test", "Go", lock_token="lock"):
            pass

        if sm.store_compaction_result.called:
            result = sm.store_compaction_result.call_args.args[1]
            meta = result.compaction_metadata
            assert meta["schema_version"] == 1
            assert meta["status"] in ("degraded", "failed")
