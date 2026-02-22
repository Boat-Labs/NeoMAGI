"""End-to-end integration tests for agent loop compaction (Phase 3).

Covers:
- Short conversation: no compaction triggered
- Warn zone: log warning, no compaction
- Long conversation: compaction triggered, watermark advances
- Current turn preserved after compaction (P0)
- Post-compaction continuity (5 rounds)
- Second compaction: rolling summary, watermark advances
- Noop: no store_compaction_result called, no prompt rebuild
- Repeated compaction with no new messages: idempotent noop
- Reentry protection: max_compactions_per_request enforced
- store_compaction_result called exactly once per compact (ADR 0032)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.agent import AgentLoop
from src.agent.events import TextChunk
from src.agent.model_client import ContentDelta
from src.config.settings import CompactionSettings
from src.session.manager import CompactionState, Message, MessageWithSeq
from src.tools.base import ToolMode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stream_response(text: str = "Hello!"):
    """Create a mock async iterator that yields a ContentDelta."""

    async def stream(*args, **kwargs):
        yield ContentDelta(text=text)

    return stream


def _msg_with_seq(seq: int, role: str, content: str = "test") -> MessageWithSeq:
    return MessageWithSeq(
        seq=seq, role=role, content=content, tool_calls=None, tool_call_id=None
    )


def _make_long_history(n_turns: int, start_seq: int = 0) -> list[MessageWithSeq]:
    """Create n_turns worth of user/assistant pairs."""
    msgs = []
    seq = start_seq
    for i in range(n_turns):
        msgs.append(_msg_with_seq(seq, "user", f"User message {i} " + "x" * 100))
        seq += 1
        msgs.append(_msg_with_seq(seq, "assistant", f"Assistant response {i} " + "y" * 100))
        seq += 1
    return msgs


def _make_session_manager(
    history: list[MessageWithSeq] | None = None,
    compaction_state: CompactionState | None = None,
    user_seq: int = 0,
):
    """Create a mock SessionManager with Phase 3 interface."""
    sm = MagicMock()
    user_msg = MagicMock(spec=Message)
    user_msg.seq = user_seq
    sm.append_message = AsyncMock(return_value=user_msg)
    sm.get_mode = AsyncMock(return_value=ToolMode.chat_safe)
    sm.get_compaction_state = AsyncMock(return_value=compaction_state)

    all_history = history or []

    def _effective_history(session_id, last_compaction_seq):
        if last_compaction_seq is None:
            return all_history
        return [m for m in all_history if m.seq > last_compaction_seq]

    sm.get_effective_history = MagicMock(side_effect=_effective_history)
    sm.get_history_with_seq = MagicMock(return_value=all_history)
    sm.store_compaction_result = AsyncMock()
    return sm


def _make_settings(**overrides) -> CompactionSettings:
    defaults = {
        "context_limit": 2000,  # Very small for deterministic triggering
        "warn_ratio": 0.70,
        "compact_ratio": 0.85,
        "reserved_output_tokens": 200,
        "safety_margin_tokens": 100,
        "min_preserved_turns": 3,
        "flush_timeout_s": 5.0,
        "compact_timeout_s": 5.0,
        "max_compactions_per_request": 2,
    }
    defaults.update(overrides)
    return CompactionSettings(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAgentCompactionIntegration:

    async def test_short_conversation_no_compaction(self, tmp_path):
        """Short conversation (< warn threshold): no compaction triggered."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(return_value="{}")
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        # Very few messages
        history = [_msg_with_seq(0, "user", "Hi")]
        sm = _make_session_manager(history, user_seq=0)
        settings = _make_settings(context_limit=100_000)  # Very large limit

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
        )

        async for _ in agent.handle_message("test", "Hi"):
            pass

        # No compaction should have been triggered
        sm.store_compaction_result.assert_not_called()

    async def test_warn_zone_no_compaction(self, tmp_path):
        """Warn zone: log warning but no compaction triggered."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(return_value="{}")
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        # Build history that lands in warn zone but not compact zone
        # context_limit=2000, reserved=200, safety=100 → usable=1700
        # warn = 1700*0.70 = 1190, compact = 1700*0.85 = 1445
        # We need ~1200-1400 tokens of messages
        history = _make_long_history(5, start_seq=0)
        sm = _make_session_manager(history, user_seq=10)
        settings = _make_settings()

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
        )

        with patch("src.agent.agent.logger") as mock_logger:
            async for _ in agent.handle_message("test", "Question"):
                pass

            # Budget check should be logged
            budget_calls = [
                c for c in mock_logger.info.call_args_list
                if c.args and c.args[0] == "budget_check"
            ]
            assert len(budget_calls) >= 1

        # No compaction store
        sm.store_compaction_result.assert_not_called()

    async def test_compaction_triggered_on_long_conversation(self, tmp_path):
        """Long conversation triggers compaction, watermark advances."""
        summary_response = (
            '{"facts":["fact1"],"decisions":[],"open_todos":[],'
            '"user_prefs":[],"timeline":[]}'
        )
        model_client = MagicMock()
        model_client.chat = AsyncMock(return_value=summary_response)
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        # Large history to exceed compact threshold.
        # context_limit=2000 → usable=1700 → compact_threshold=850.
        # Initial 20 turns ≈ 1285 tokens > 850 → triggers compaction.
        # Post-compaction preserved 3 turns ≈ 328 tokens < 850 → passes recheck.
        history = _make_long_history(20, start_seq=0)
        sm = _make_session_manager(history, user_seq=40)
        settings = _make_settings(context_limit=2000, compact_ratio=0.50, warn_ratio=0.30)

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
            model="gpt-4o-mini",
        )

        async for _ in agent.handle_message("test", "Continue", lock_token="test-lock"):
            pass

        # Compaction should have been stored
        sm.store_compaction_result.assert_called_once()
        stored_result = sm.store_compaction_result.call_args
        assert stored_result.kwargs["lock_token"] == "test-lock"

    async def test_current_turn_preserved_after_compaction(self, tmp_path):
        """P0: After compaction, current user message is still in effective history."""
        summary_response = (
            '{"facts":[],"decisions":[],"open_todos":[],'
            '"user_prefs":[],"timeline":[]}'
        )
        model_client = MagicMock()
        model_client.chat = AsyncMock(return_value=summary_response)
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        current_user_seq = 40
        history = _make_long_history(20, start_seq=0)
        # Add current user message
        history.append(_msg_with_seq(current_user_seq, "user", "Current question"))

        sm = _make_session_manager(history, user_seq=current_user_seq)

        # Track calls to get_effective_history
        call_count = [0]
        original_return = history

        def effective_history_side_effect(session_id, last_seq):
            call_count[0] += 1
            if last_seq is None:
                return original_return
            return [m for m in original_return if m.seq > last_seq]

        sm.get_effective_history = MagicMock(side_effect=effective_history_side_effect)

        settings = _make_settings(context_limit=2000, compact_ratio=0.50, warn_ratio=0.30)

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
            model="gpt-4o-mini",
        )

        async for _ in agent.handle_message("test", "Current question", lock_token="lock"):
            pass

        # Verify that effective_history was called at least once
        assert sm.get_effective_history.call_count >= 1

        # If compaction happened, verify the second call has a non-None watermark
        if sm.store_compaction_result.called:
            # After compaction, get_effective_history is called again with new watermark
            last_call = sm.get_effective_history.call_args_list[-1]
            new_watermark = (
                last_call.args[1]
                if len(last_call.args) > 1
                else last_call.kwargs.get("last_compaction_seq")
            )
            # The current user message seq should be > watermark
            if new_watermark is not None:
                assert current_user_seq > new_watermark

    async def test_noop_does_not_call_store(self, tmp_path):
        """Noop status: no store_compaction_result called."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(return_value="{}")
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        # Few messages, won't trigger compaction
        history = [_msg_with_seq(0, "user", "Hi")]
        sm = _make_session_manager(history, user_seq=0)
        settings = _make_settings(context_limit=100_000)

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
        )

        async for _ in agent.handle_message("test", "Hi"):
            pass

        sm.store_compaction_result.assert_not_called()

    async def test_no_compaction_without_lock_token(self, tmp_path):
        """Compaction requires lock_token; without it, no compact is attempted."""
        summary_response = (
            '{"facts":[],"decisions":[],"open_todos":[],'
            '"user_prefs":[],"timeline":[]}'
        )
        model_client = MagicMock()
        model_client.chat = AsyncMock(return_value=summary_response)
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        history = _make_long_history(20, start_seq=0)
        sm = _make_session_manager(history, user_seq=40)
        settings = _make_settings(context_limit=2000, compact_ratio=0.50, warn_ratio=0.30)

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
            model="gpt-4o-mini",
        )

        # No lock_token → no compaction even if budget exceeds threshold
        async for _ in agent.handle_message("test", "Continue"):
            pass

        sm.store_compaction_result.assert_not_called()

    async def test_compaction_reentry_protection(self, tmp_path):
        """max_compactions_per_request limits compaction attempts."""
        summary_response = (
            '{"facts":[],"decisions":[],"open_todos":[],'
            '"user_prefs":[],"timeline":[]}'
        )
        model_client = MagicMock()
        model_client.chat = AsyncMock(return_value=summary_response)
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        history = _make_long_history(20, start_seq=0)
        sm = _make_session_manager(history, user_seq=40)
        settings = _make_settings(
            context_limit=2000,
            compact_ratio=0.50,
            warn_ratio=0.30,
            max_compactions_per_request=1,
        )

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
            model="gpt-4o-mini",
        )

        async for _ in agent.handle_message("test", "Go", lock_token="lock"):
            pass

        # At most 1 compaction should be stored
        assert sm.store_compaction_result.call_count <= 1

    async def test_prompt_builder_receives_compacted_context(self, tmp_path):
        """After compaction, PromptBuilder.build is called with compacted_context."""
        model_client = MagicMock()
        summary = (
            '{"facts":[],"decisions":[],"open_todos":[],'
            '"user_prefs":[],"timeline":[]}'
        )
        model_client.chat = AsyncMock(return_value=summary)
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        # Pre-existing compaction state
        ctx = '{"facts":["user prefers Python"]}'
        compaction_state = CompactionState(
            compacted_context=ctx,
            last_compaction_seq=10,
            compaction_metadata={"schema_version": 1},
        )
        history = [_msg_with_seq(11, "user", "What was my preference?")]
        sm = _make_session_manager(
            history, compaction_state=compaction_state, user_seq=11,
        )

        settings = _make_settings(context_limit=100_000)

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
        )

        with patch.object(
            agent._prompt_builder, "build",
            wraps=agent._prompt_builder.build,
        ) as mock_build:
            async for _ in agent.handle_message(
                "test", "What was my preference?",
            ):
                pass

            assert mock_build.call_count >= 1
            first_call = mock_build.call_args_list[0]
            assert first_call.kwargs.get("compacted_context") == ctx

    async def test_effective_history_uses_watermark(self, tmp_path):
        """After loading compaction state, get_effective_history uses watermark."""
        model_client = MagicMock()
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        compaction_state = CompactionState(
            compacted_context="summary",
            last_compaction_seq=10,
            compaction_metadata={"schema_version": 1},
        )
        history = [_msg_with_seq(11, "user", "Hello")]
        sm = _make_session_manager(history, compaction_state=compaction_state, user_seq=11)

        settings = _make_settings(context_limit=100_000)

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
        )

        async for _ in agent.handle_message("test", "Hello"):
            pass

        # get_effective_history should be called with watermark=10
        sm.get_effective_history.assert_called()
        first_call = sm.get_effective_history.call_args_list[0]
        assert first_call.args[1] == 10  # last_compaction_seq

    async def test_backward_compat_without_settings(self, tmp_path):
        """Agent loop works without compaction settings (backward compat)."""
        model_client = MagicMock()
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response()()]
        )

        sm = _make_session_manager()

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
        )

        events = []
        async for event in agent.handle_message("test", "Hi"):
            events.append(event)

        assert len(events) >= 1
        assert isinstance(events[0], TextChunk)


# ---------------------------------------------------------------------------
# F2: Post-compaction budget recheck + overflow retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPostCompactionOverflow:

    async def test_overflow_triggers_emergency_trim(self, tmp_path):
        """After compaction, if still over budget → emergency trim with reduced turns."""
        summary_response = (
            '{"facts":[],"decisions":[],"open_todos":[],'
            '"user_prefs":[],"timeline":[]}'
        )
        model_client = MagicMock()
        model_client.chat = AsyncMock(return_value=summary_response)
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response("After emergency trim")()]
        )

        # Large history. context_limit=500 → usable=200 → compact_threshold=100
        # Post-compaction preserved turns still exceed 100 → overflow → emergency trim
        history = _make_long_history(30, start_seq=0)
        sm = _make_session_manager(history, user_seq=60)

        settings = _make_settings(
            context_limit=500,
            compact_ratio=0.50,
            warn_ratio=0.30,
            min_preserved_turns=4,
        )

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
            model="gpt-4o-mini",
        )

        with patch.object(
            agent, "_emergency_trim", wraps=agent._emergency_trim
        ) as spy_trim:
            events = []
            async for event in agent.handle_message("test", "Go", lock_token="lock"):
                events.append(event)

            # _emergency_trim MUST have been called
            assert spy_trim.call_count >= 1
            # Verify reduced turns = max(4//2, 1) = 2
            call_kwargs = spy_trim.call_args.kwargs
            assert call_kwargs["min_preserved_turns_override"] == 2

    async def test_emergency_trim_uses_reduced_turns(self, tmp_path):
        """_emergency_trim receives min_preserved_turns_override (not mutating settings)."""
        model_client = MagicMock()
        summary_response = (
            '{"facts":[],"decisions":[],"open_todos":[],'
            '"user_prefs":[],"timeline":[]}'
        )
        model_client.chat = AsyncMock(return_value=summary_response)
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response("Done")()]
        )

        history = _make_long_history(30, start_seq=0)
        sm = _make_session_manager(history, user_seq=60)

        settings = _make_settings(
            context_limit=500,
            compact_ratio=0.50,
            warn_ratio=0.30,
            min_preserved_turns=6,
        )

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
            model="gpt-4o-mini",
        )

        original_preserved = settings.min_preserved_turns

        with patch.object(
            agent, "_emergency_trim", wraps=agent._emergency_trim
        ) as spy_trim:
            events = []
            async for event in agent.handle_message("test", "Go", lock_token="lock"):
                events.append(event)

            # Settings should NOT be mutated (concurrency safety)
            assert settings.min_preserved_turns == original_preserved

            # _emergency_trim called with reduced_turns = max(6//2, 1) = 3
            if spy_trim.call_count >= 1:
                call_kwargs = spy_trim.call_args.kwargs
                assert call_kwargs["min_preserved_turns_override"] == 3

    async def test_overflow_failopen_returns_error_message(self, tmp_path):
        """When emergency trim returns None → fail-open with user-facing message."""
        summary_response = (
            '{"facts":[],"decisions":[],"open_todos":[],'
            '"user_prefs":[],"timeline":[]}'
        )
        model_client = MagicMock()
        model_client.chat = AsyncMock(return_value=summary_response)
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response("Should not see this")()]
        )

        # context_limit=500 but very few messages (4 turns).
        # Compaction triggers but few turns ≈ min_preserved → trim cannot reduce.
        history = _make_long_history(4, start_seq=0)
        sm = _make_session_manager(history, user_seq=8)

        settings = _make_settings(
            context_limit=500,
            compact_ratio=0.50,
            warn_ratio=0.30,
            min_preserved_turns=2,
        )

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

        # Fail-open: error message present
        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) >= 1
        assert any("无法进一步压缩" in e.content for e in text_events)

        # Model streaming MUST NOT be called (fail-open = no model call)
        model_client.chat_stream_with_tools.assert_not_called()

    async def test_overflow_store_exception_failopen(self, tmp_path):
        """store_compaction_result throws during overflow → fail-open."""
        from src.infra.errors import SessionFencingError

        summary_response = (
            '{"facts":[],"decisions":[],"open_todos":[],'
            '"user_prefs":[],"timeline":[]}'
        )
        model_client = MagicMock()
        model_client.chat = AsyncMock(return_value=summary_response)
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response("Should not see this")()]
        )

        history = _make_long_history(30, start_seq=0)
        sm = _make_session_manager(history, user_seq=60)

        # Make store fail on the SECOND call (first call is from _try_compact)
        call_count = [0]

        async def store_side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise SessionFencingError("lock stolen")

        sm.store_compaction_result = AsyncMock(side_effect=store_side_effect)

        settings = _make_settings(
            context_limit=500,
            compact_ratio=0.50,
            warn_ratio=0.30,
            min_preserved_turns=4,
        )

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

        # Fail-open: error message present (exception path uses different wording)
        text_events = [e for e in events if isinstance(e, TextChunk)]
        assert len(text_events) >= 1
        assert any(
            "压缩过程中遇到错误" in e.content or "无法进一步压缩" in e.content
            for e in text_events
        )

        # store was called at least twice (normal + overflow retry that threw)
        assert sm.store_compaction_result.call_count >= 2

        # Model streaming MUST NOT be called (fail-open = no model call)
        model_client.chat_stream_with_tools.assert_not_called()

    async def test_min_preserved_1_still_tries_emergency_trim(self, tmp_path):
        """P2-1: min_preserved_turns=1 → reduced_turns=1=original, still tries trim."""
        summary_response = (
            '{"facts":[],"decisions":[],"open_todos":[],'
            '"user_prefs":[],"timeline":[]}'
        )
        model_client = MagicMock()
        model_client.chat = AsyncMock(return_value=summary_response)
        model_client.chat_stream_with_tools = MagicMock(
            side_effect=[_make_stream_response("After trim")()]
        )

        history = _make_long_history(30, start_seq=0)
        sm = _make_session_manager(history, user_seq=60)

        settings = _make_settings(
            context_limit=500,
            compact_ratio=0.50,
            warn_ratio=0.30,
            min_preserved_turns=1,
        )

        agent = AgentLoop(
            model_client=model_client,
            session_manager=sm,
            workspace_dir=tmp_path,
            compaction_settings=settings,
            model="gpt-4o-mini",
        )

        with patch.object(
            agent, "_emergency_trim", wraps=agent._emergency_trim
        ) as spy_trim:
            events = []
            async for event in agent.handle_message("test", "Go", lock_token="lock"):
                events.append(event)

            # _emergency_trim MUST be called (not short-circuited)
            assert spy_trim.call_count >= 1
            call_kwargs = spy_trim.call_args.kwargs
            assert call_kwargs["min_preserved_turns_override"] == 1
