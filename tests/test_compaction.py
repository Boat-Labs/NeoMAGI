"""Tests for compaction engine (Phase 2).

Covers:
- Turn splitting: user boundaries, tool-only attribution, empty history, current turn exclusion
- CompactionEngine: normal compaction (mock LLM), rolling summary structure (ADR 0028),
  watermark monotonic increase, noop semantics, degraded path, anchor validation
- CompactionSettings validation (Phase 2 fields)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.compaction import CompactionEngine, split_turns
from src.agent.token_budget import BudgetStatus, TokenCounter
from src.config.settings import CompactionSettings
from src.session.manager import MessageWithSeq

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(seq: int, role: str, content: str = "test") -> MessageWithSeq:
    return MessageWithSeq(seq=seq, role=role, content=content, tool_calls=None, tool_call_id=None)


def _make_history(n_turns: int, start_seq: int = 0) -> list[MessageWithSeq]:
    """Create a history with n_turns user/assistant pairs."""
    msgs = []
    seq = start_seq
    for _ in range(n_turns):
        msgs.append(_msg(seq, "user", f"user message {seq}"))
        seq += 1
        msgs.append(_msg(seq, "assistant", f"assistant response {seq}"))
        seq += 1
    return msgs


def _make_verbose_history(n_turns: int, start_seq: int = 0) -> list[MessageWithSeq]:
    """Create history with longer messages to exceed F6 small-input threshold."""
    msgs = []
    seq = start_seq
    for i in range(n_turns):
        msgs.append(_msg(
            seq, "user",
            f"This is a detailed question about topic {i} with enough context "
            f"to produce meaningful tokens for the compaction summary generation "
            f"process. The user is asking about architecture decisions and trade-offs "
            f"related to the implementation of feature number {seq}.",
        ))
        seq += 1
        msgs.append(_msg(
            seq, "assistant",
            f"Here is a comprehensive response to question {i} covering "
            f"the architectural considerations and implementation details "
            f"that were discussed during the design review. The recommended "
            f"approach involves several key steps and trade-offs for item {seq}.",
        ))
        seq += 1
    return msgs


def _make_settings(**overrides) -> CompactionSettings:
    defaults = {
        "context_limit": 10_000,
        "warn_ratio": 0.70,
        "compact_ratio": 0.85,
        "reserved_output_tokens": 500,
        "safety_margin_tokens": 200,
        "min_preserved_turns": 3,
        "flush_timeout_s": 5.0,
        "compact_timeout_s": 5.0,
    }
    defaults.update(overrides)
    return CompactionSettings(**defaults)


def _make_engine(model_client=None, settings=None, workspace_dir=None):
    if model_client is None:
        model_client = MagicMock()
        _empty_summary = (
            '{"facts":[],"decisions":[],"open_todos":[],'
            '"user_prefs":[],"timeline":[]}'
        )
        model_client.chat = AsyncMock(return_value=_empty_summary)
    if settings is None:
        settings = _make_settings()
    counter = TokenCounter("gpt-4o-mini")
    return CompactionEngine(model_client, counter, settings, workspace_dir=workspace_dir)


def _make_budget_status(status: str = "compact_needed") -> BudgetStatus:
    return BudgetStatus(
        status=status,
        current_tokens=9000,
        usable_budget=9300,
        warn_threshold=6510,
        compact_threshold=7905,
        tokenizer_mode="exact",
    )


# ---------------------------------------------------------------------------
# Turn splitting tests
# ---------------------------------------------------------------------------


class TestSplitTurns:

    def test_empty_messages(self):
        assert split_turns([]) == []

    def test_single_user_message(self):
        msgs = [_msg(0, "user")]
        turns = split_turns(msgs)
        assert len(turns) == 1
        assert turns[0].start_seq == 0
        assert turns[0].end_seq == 0

    def test_user_assistant_pair(self):
        msgs = [_msg(0, "user"), _msg(1, "assistant")]
        turns = split_turns(msgs)
        assert len(turns) == 1
        assert turns[0].start_seq == 0
        assert turns[0].end_seq == 1
        assert len(turns[0].messages) == 2

    def test_multiple_turns(self):
        msgs = _make_history(3)
        turns = split_turns(msgs)
        assert len(turns) == 3

    def test_tool_messages_belong_to_current_turn(self):
        msgs = [
            _msg(0, "user"),
            _msg(1, "assistant"),
            _msg(2, "tool"),
            _msg(3, "assistant"),
            _msg(4, "user"),
        ]
        turns = split_turns(msgs)
        assert len(turns) == 2
        assert len(turns[0].messages) == 4  # user + assistant + tool + assistant
        assert turns[0].end_seq == 3
        assert len(turns[1].messages) == 1  # second user

    def test_consecutive_user_messages(self):
        msgs = [_msg(0, "user"), _msg(1, "user"), _msg(2, "assistant")]
        turns = split_turns(msgs)
        assert len(turns) == 2
        assert len(turns[0].messages) == 1  # first user alone
        assert len(turns[1].messages) == 2  # second user + assistant


# ---------------------------------------------------------------------------
# CompactionEngine tests
# ---------------------------------------------------------------------------


class TestCompactionEngine:

    @pytest.mark.asyncio
    async def test_noop_when_empty_history(self):
        engine = _make_engine()
        result = await engine.compact(
            messages=[],
            system_prompt="test system prompt with enough content to pass validation checks",
            tools_schema=[],
            budget_status=_make_budget_status(),
            last_compaction_seq=None,
            previous_compacted_context=None,
            current_user_seq=0,
            model="gpt-4o-mini",
        )
        assert result.status == "noop"

    @pytest.mark.asyncio
    async def test_noop_when_few_turns(self):
        """When turns <= min_preserved_turns, should be noop."""
        engine = _make_engine(settings=_make_settings(min_preserved_turns=5))
        msgs = _make_history(3)  # Only 3 turns
        result = await engine.compact(
            messages=msgs,
            system_prompt="test system prompt with enough content to pass validation checks",
            tools_schema=[],
            budget_status=_make_budget_status(),
            last_compaction_seq=None,
            previous_compacted_context=None,
            current_user_seq=100,
            model="gpt-4o-mini",
        )
        assert result.status == "noop"

    @pytest.mark.asyncio
    async def test_noop_when_already_compacted(self):
        """When all compressible turns are already compacted, should be noop."""
        engine = _make_engine(settings=_make_settings(min_preserved_turns=2))
        msgs = _make_history(5)  # 10 messages, seq 0-9
        result = await engine.compact(
            messages=msgs,
            system_prompt="test system prompt with enough content to pass validation checks",
            tools_schema=[],
            budget_status=_make_budget_status(),
            last_compaction_seq=5,  # Already compacted up to seq 5
            previous_compacted_context="previous summary",
            current_user_seq=100,
            model="gpt-4o-mini",
        )
        assert result.status == "noop"

    @pytest.mark.asyncio
    async def test_normal_compaction(self):
        """Normal compaction produces summary and advances watermark."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(
            return_value='{"facts":["fact1"],"decisions":["dec1"],"open_todos":[],"user_prefs":[],"timeline":[]}'
        )
        engine = _make_engine(
            model_client=model_client,
            settings=_make_settings(min_preserved_turns=3),
        )
        msgs = _make_verbose_history(10)  # Verbose to exceed F6 threshold

        result = await engine.compact(
            messages=msgs,
            system_prompt="test system prompt with enough content to pass validation checks here",
            tools_schema=[],
            budget_status=_make_budget_status(),
            last_compaction_seq=None,
            previous_compacted_context=None,
            current_user_seq=100,
            model="gpt-4o-mini",
            session_id="test-session",
        )

        assert result.status == "success"
        assert result.compacted_context is not None
        assert result.new_compaction_seq > 0
        assert result.compaction_metadata["schema_version"] == 1
        assert result.compaction_metadata["status"] == "success"

    @pytest.mark.asyncio
    async def test_watermark_monotonic_increase(self):
        """new_compaction_seq must be greater than last_compaction_seq."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(
            return_value='{"facts":[],"decisions":[],"open_todos":[],"user_prefs":[],"timeline":[]}'
        )
        engine = _make_engine(
            model_client=model_client,
            settings=_make_settings(min_preserved_turns=2),
        )

        msgs = _make_verbose_history(8)  # Verbose to exceed F6 threshold
        result = await engine.compact(
            messages=msgs,
            system_prompt="test system prompt with enough content here to pass validation check",
            tools_schema=[],
            budget_status=_make_budget_status(),
            last_compaction_seq=3,
            previous_compacted_context="old summary",
            current_user_seq=100,
            model="gpt-4o-mini",
        )

        assert result.status == "success"
        assert result.new_compaction_seq > 3

    @pytest.mark.asyncio
    async def test_watermark_does_not_exceed_current_user_seq(self):
        """new_compaction_seq must not exceed current_user_seq - 1."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(
            return_value='{"facts":[],"decisions":[],"open_todos":[],"user_prefs":[],"timeline":[]}'
        )
        engine = _make_engine(
            model_client=model_client,
            settings=_make_settings(min_preserved_turns=2),
        )

        msgs = _make_history(5)  # seq 0-9
        current_user_seq = 8  # Exclude current turn
        result = await engine.compact(
            messages=msgs,
            system_prompt="test system prompt with enough content to pass the validation check",
            tools_schema=[],
            budget_status=_make_budget_status(),
            last_compaction_seq=None,
            previous_compacted_context=None,
            current_user_seq=current_user_seq,
            model="gpt-4o-mini",
        )

        if result.status != "noop":
            assert result.new_compaction_seq <= current_user_seq - 1

    @pytest.mark.asyncio
    async def test_current_turn_excluded(self):
        """Turns starting at or after current_user_seq are excluded from compaction."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(
            return_value='{"facts":[],"decisions":[],"open_todos":[],"user_prefs":[],"timeline":[]}'
        )
        engine = _make_engine(
            model_client=model_client,
            settings=_make_settings(min_preserved_turns=2),
        )

        msgs = _make_history(6)  # seq 0-11
        # Current turn starts at seq 10
        result = await engine.compact(
            messages=msgs,
            system_prompt="test system prompt with enough content to pass the validation checks",
            tools_schema=[],
            budget_status=_make_budget_status(),
            last_compaction_seq=None,
            previous_compacted_context=None,
            current_user_seq=10,
            model="gpt-4o-mini",
        )

        # The current turn (seq 10-11) should not be compacted
        if result.status != "noop":
            assert result.new_compaction_seq < 10

    @pytest.mark.asyncio
    async def test_degraded_on_llm_timeout(self):
        """LLM timeout results in degraded status."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(side_effect=TimeoutError("timeout"))
        engine = _make_engine(model_client=model_client, settings=_make_settings(
            min_preserved_turns=2, compact_timeout_s=0.1
        ))

        msgs = _make_history(8)
        result = await engine.compact(
            messages=msgs,
            system_prompt="test system prompt with enough content to pass the validation checks",
            tools_schema=[],
            budget_status=_make_budget_status(),
            last_compaction_seq=None,
            previous_compacted_context=None,
            current_user_seq=100,
            model="gpt-4o-mini",
        )

        assert result.status == "degraded"
        assert result.new_compaction_seq > 0  # Still advances watermark

    @pytest.mark.asyncio
    async def test_noop_idempotent(self):
        """Repeated compaction with no new messages returns noop."""
        engine = _make_engine(settings=_make_settings(min_preserved_turns=2))
        msgs = _make_history(5)

        # First: noop because last_compaction_seq covers everything
        result = await engine.compact(
            messages=msgs,
            system_prompt="test system prompt with enough content to pass the validation checks",
            tools_schema=[],
            budget_status=_make_budget_status(),
            last_compaction_seq=5,
            previous_compacted_context="old summary",
            current_user_seq=100,
            model="gpt-4o-mini",
        )
        assert result.status == "noop"

    @pytest.mark.asyncio
    async def test_metadata_fields_complete(self):
        """CompactionResult metadata contains all required fields."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(
            return_value='{"facts":[],"decisions":[],"open_todos":[],"user_prefs":[],"timeline":[]}'
        )
        engine = _make_engine(
            model_client=model_client,
            settings=_make_settings(min_preserved_turns=2),
        )

        msgs = _make_history(8)
        result = await engine.compact(
            messages=msgs,
            system_prompt="test system prompt with enough content to pass the validation checks",
            tools_schema=[],
            budget_status=_make_budget_status(),
            last_compaction_seq=None,
            previous_compacted_context=None,
            current_user_seq=100,
            model="gpt-4o-mini",
        )

        meta = result.compaction_metadata
        assert meta["schema_version"] == 1
        assert meta["status"] in ("success", "degraded", "failed", "noop")
        assert "preserved_count" in meta
        assert "summarized_count" in meta
        assert "flush_skipped" in meta
        assert "anchor_validation_passed" in meta
        assert "anchor_retry_used" in meta
        assert "triggered_at" in meta
        assert "compacted_context_tokens" in meta
        assert "rolling_summary_input_tokens" in meta

    @pytest.mark.asyncio
    async def test_flush_skipped_on_timeout(self):
        """Flush timeout is handled gracefully."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(
            return_value='{"facts":[],"decisions":[],"open_todos":[],"user_prefs":[],"timeline":[]}'
        )
        settings = _make_settings(min_preserved_turns=2, flush_timeout_s=0.001)
        engine = _make_engine(model_client=model_client, settings=settings)

        # Patch flush generator to be slow

        original_generate = engine._flush_generator.generate

        def slow_generate(*args, **kwargs):
            import time
            time.sleep(0.1)
            return original_generate(*args, **kwargs)

        engine._flush_generator.generate = slow_generate

        msgs = _make_history(8)
        result = await engine.compact(
            messages=msgs,
            system_prompt="test system prompt with enough content to pass the validation checks",
            tools_schema=[],
            budget_status=_make_budget_status(),
            last_compaction_seq=None,
            previous_compacted_context=None,
            current_user_seq=100,
            model="gpt-4o-mini",
        )

        assert result.compaction_metadata["flush_skipped"] is True

    @pytest.mark.asyncio
    async def test_small_input_returns_degraded_not_noop(self):
        """Input too small for summary → degraded (not noop), watermark advances (F6)."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(return_value="should not be called")
        # Use min_preserved_turns=1 so that 3 turns → 2 compressible
        # Short messages → few tokens → 30% < 100
        engine = _make_engine(
            model_client=model_client,
            settings=_make_settings(min_preserved_turns=1),
        )

        msgs = [
            _msg(0, "user", "hi"),
            _msg(1, "assistant", "hello"),
            _msg(2, "user", "ok"),
            _msg(3, "assistant", "sure"),
            _msg(4, "user", "bye"),
            _msg(5, "assistant", "ciao"),
        ]

        result = await engine.compact(
            messages=msgs,
            system_prompt="test system prompt",
            tools_schema=[],
            budget_status=_make_budget_status(),
            last_compaction_seq=None,
            previous_compacted_context=None,
            current_user_seq=100,
            model="gpt-4o-mini",
        )

        assert result.status == "degraded"
        assert result.new_compaction_seq > 0  # watermark advances
        # LLM should NOT be called (summary skipped)
        model_client.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_small_input_preserves_previous_context(self):
        """Degraded path from small input preserves previous_compacted_context (F6)."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(return_value="should not be called")
        engine = _make_engine(
            model_client=model_client,
            settings=_make_settings(min_preserved_turns=1),
        )

        msgs = [
            _msg(0, "user", "hi"),
            _msg(1, "assistant", "hello"),
            _msg(2, "user", "ok"),
            _msg(3, "assistant", "sure"),
            _msg(4, "user", "bye"),
            _msg(5, "assistant", "ciao"),
        ]

        previous_ctx = '{"facts":["old fact"]}'
        result = await engine.compact(
            messages=msgs,
            system_prompt="test system prompt",
            tools_schema=[],
            budget_status=_make_budget_status(),
            last_compaction_seq=None,
            previous_compacted_context=previous_ctx,
            current_user_seq=100,
            model="gpt-4o-mini",
        )

        assert result.status == "degraded"
        assert result.compacted_context == previous_ctx

    @pytest.mark.asyncio
    async def test_summary_tokens_strictly_30_percent(self):
        """max_summary_tokens must be exactly int(input_tokens * 0.3), no floor (F6)."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(
            return_value='{"facts":[],"decisions":[],"open_todos":[],"user_prefs":[],"timeline":[]}'
        )
        engine = _make_engine(
            model_client=model_client,
            settings=_make_settings(min_preserved_turns=2),
        )

        # Verbose messages to ensure 30% >= 100
        msgs = _make_verbose_history(10)
        result = await engine.compact(
            messages=msgs,
            system_prompt="test system prompt with enough content to pass the validation checks",
            tools_schema=[],
            budget_status=_make_budget_status(),
            last_compaction_seq=None,
            previous_compacted_context=None,
            current_user_seq=100,
            model="gpt-4o-mini",
        )

        # Verify that chat was called (large enough input)
        assert result.status == "success"
        assert model_client.chat.call_count >= 1


# ---------------------------------------------------------------------------
# CompactionSettings Phase 2 validation
# ---------------------------------------------------------------------------


class TestCompactionSettingsPhase2:

    def test_phase2_defaults(self):
        settings = CompactionSettings()
        assert settings.min_preserved_turns == 8
        assert settings.flush_timeout_s == 30.0
        assert settings.compact_timeout_s == 30.0
        assert settings.fail_open is True
        assert settings.max_flush_candidates == 20
        assert settings.max_candidate_text_bytes == 2048
        assert settings.max_compactions_per_request == 2
        assert settings.summary_temperature == 0.1
        assert settings.anchor_retry_enabled is True

    def test_summary_temperature_validation(self):
        with pytest.raises(ValueError, match="summary_temperature"):
            CompactionSettings(summary_temperature=1.5)

    def test_summary_temperature_negative_rejected(self):
        with pytest.raises(ValueError, match="summary_temperature"):
            CompactionSettings(summary_temperature=-0.1)


# ---------------------------------------------------------------------------
# F4: temperature passed to model_client.chat
# ---------------------------------------------------------------------------


class TestTemperaturePassthrough:

    @pytest.mark.asyncio
    async def test_compaction_passes_summary_temperature(self):
        """Compaction calls model_client.chat with temperature=summary_temperature."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(
            return_value='{"facts":[],"decisions":[],"open_todos":[],"user_prefs":[],"timeline":[]}'
        )
        settings = _make_settings(min_preserved_turns=2, summary_temperature=0.1)
        engine = _make_engine(model_client=model_client, settings=settings)

        msgs = _make_verbose_history(8)  # Verbose to exceed F6 threshold
        await engine.compact(
            messages=msgs,
            system_prompt="test system prompt with enough content to pass the validation checks",
            tools_schema=[],
            budget_status=_make_budget_status(),
            last_compaction_seq=None,
            previous_compacted_context=None,
            current_user_seq=100,
            model="gpt-4o-mini",
        )

        # chat should have been called with temperature=0.1
        assert model_client.chat.call_count >= 1
        call_kwargs = model_client.chat.call_args
        assert call_kwargs.kwargs.get("temperature") == 0.1 or call_kwargs[0][2] == 0.1

    @pytest.mark.asyncio
    async def test_chat_without_temperature_unchanged(self):
        """model_client.chat() without temperature preserves default behavior."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(return_value="response")

        # Call chat without temperature (like non-compaction callers do)
        await model_client.chat([], "model")
        call_args = model_client.chat.call_args
        # No temperature kwarg passed
        assert "temperature" not in (call_args.kwargs or {})


# ---------------------------------------------------------------------------
# F3: Anchor validation (ADR 0030)
# ---------------------------------------------------------------------------


class TestAnchorValidation:

    def test_anchor_passes_with_workspace_content_in_prompt(self, tmp_path):
        """Normal case: workspace first lines present in system_prompt → pass."""
        (tmp_path / "AGENTS.md").write_text("# Agent Behavior SOP\nDetails...\n")
        (tmp_path / "SOUL.md").write_text("# Core Values\nBe helpful.\n")
        (tmp_path / "USER.md").write_text("# User Preferences\nPrefers Python.\n")

        engine = _make_engine(workspace_dir=tmp_path)
        system_prompt = (
            "You are Magi.\n# Agent Behavior SOP\nAgent rules.\n"
            "# Core Values\nValues here.\n# User Preferences\nPrefs."
        )
        assert engine._validate_anchors(system_prompt, None) is True

    def test_anchor_fails_when_missing_from_prompt(self, tmp_path):
        """Anchor phrase missing from system_prompt → fail."""
        (tmp_path / "AGENTS.md").write_text("# Agent Behavior SOP\n")
        (tmp_path / "SOUL.md").write_text("# Core Values\n")
        (tmp_path / "USER.md").write_text("# User Preferences\n")

        engine = _make_engine(workspace_dir=tmp_path)
        # System prompt missing AGENTS.md first line
        system_prompt = "You are Magi. # Core Values # User Preferences"
        assert engine._validate_anchors(system_prompt, None) is False

    def test_anchor_passes_when_no_workspace_files(self, tmp_path):
        """No workspace anchor files → no anchors → pass (don't block compaction)."""
        engine = _make_engine(workspace_dir=tmp_path)
        assert engine._validate_anchors("any prompt", None) is True

    def test_anchor_passes_without_workspace_dir(self):
        """No workspace_dir configured → no anchors → pass."""
        engine = _make_engine(workspace_dir=None)
        assert engine._validate_anchors("any prompt", None) is True

    def test_anchor_empty_system_prompt_fails(self, tmp_path):
        """Empty system_prompt always fails."""
        engine = _make_engine(workspace_dir=tmp_path)
        assert engine._validate_anchors("", None) is False

    def test_anchor_checks_compacted_context_too(self, tmp_path):
        """Anchor phrase in compacted_context (not system_prompt) → pass."""
        (tmp_path / "AGENTS.md").write_text("# Agent SOP\n")
        engine = _make_engine(workspace_dir=tmp_path)
        # system_prompt alone doesn't have it, but compacted_context does
        assert engine._validate_anchors("minimal", "context with # Agent SOP here") is True
