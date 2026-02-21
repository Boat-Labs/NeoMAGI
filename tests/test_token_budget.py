"""Tests for token budget infrastructure (Phase 1).

Covers:
- TokenCounter: exact/estimate modes, multi-encoding, CJK text, tools schema, per-message overhead
- BudgetTracker: threshold boundaries, status transitions
- CompactionSettings: ratio validation, usable budget validation
"""

from __future__ import annotations

import pytest

from src.agent.token_budget import BudgetStatus, BudgetTracker, TokenCounter
from src.config.settings import CompactionSettings


# ---------------------------------------------------------------------------
# TokenCounter tests
# ---------------------------------------------------------------------------


class TestTokenCounterExactMode:
    """TokenCounter with a known OpenAI model (tiktoken available)."""

    def test_exact_mode_for_openai_model(self):
        counter = TokenCounter("gpt-4o-mini")
        assert counter.tokenizer_mode == "exact"

    def test_count_text_english(self):
        counter = TokenCounter("gpt-4o-mini")
        tokens = counter.count_text("Hello, world!")
        assert tokens > 0
        assert isinstance(tokens, int)

    def test_count_text_chinese(self):
        counter = TokenCounter("gpt-4o-mini")
        tokens = counter.count_text("你好世界")
        assert tokens > 0

    def test_count_text_mixed(self):
        counter = TokenCounter("gpt-4o-mini")
        tokens = counter.count_text("Hello 你好 world 世界")
        assert tokens > 0

    def test_count_text_empty(self):
        counter = TokenCounter("gpt-4o-mini")
        assert counter.count_text("") == 0

    def test_count_messages_basic(self):
        counter = TokenCounter("gpt-4o-mini")
        messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Hi"},
        ]
        tokens = counter.count_messages(messages)
        # Should be positive and include overhead
        assert tokens > 0
        # Should be more than just the text tokens (overhead included)
        text_only = counter.count_text("You are a helpful assistant.") + counter.count_text("Hi")
        assert tokens > text_only

    def test_count_messages_with_system(self):
        """System message overhead is included in count."""
        counter = TokenCounter("gpt-4o-mini")
        with_system = counter.count_messages([
            {"role": "system", "content": "System prompt here."},
            {"role": "user", "content": "Hello"},
        ])
        without_system = counter.count_messages([
            {"role": "user", "content": "Hello"},
        ])
        assert with_system > without_system

    def test_count_messages_with_tool_calls(self):
        counter = TokenCounter("gpt-4o-mini")
        messages = [
            {"role": "user", "content": "What's the weather?"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "NYC"}'},
                    }
                ],
            },
            {"role": "tool", "content": '{"temp": 72}', "tool_call_id": "call_1"},
        ]
        tokens = counter.count_messages(messages)
        assert tokens > 0

    def test_count_tools_schema(self):
        counter = TokenCounter("gpt-4o-mini")
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "description": "Get weather",
                    "parameters": {
                        "type": "object",
                        "properties": {"city": {"type": "string"}},
                    },
                },
            }
        ]
        tokens = counter.count_tools_schema(tools)
        assert tokens > 0

    def test_count_tools_schema_empty(self):
        counter = TokenCounter("gpt-4o-mini")
        assert counter.count_tools_schema([]) == 0


class TestTokenCounterFallbackMode:
    """TokenCounter fallback when tiktoken encoding is unavailable."""

    def test_estimate_mode_for_unknown_model(self):
        counter = TokenCounter("some-unknown-model-xyz")
        assert counter.tokenizer_mode == "estimate"

    def test_count_text_estimate(self):
        counter = TokenCounter("some-unknown-model-xyz")
        text = "Hello world test"
        tokens = counter.count_text(text)
        # ceil(len("Hello world test") / 4) = ceil(16/4) = 4
        import math

        assert tokens == math.ceil(len(text) / 4)

    def test_count_text_chinese_estimate(self):
        counter = TokenCounter("some-unknown-model-xyz")
        text = "你好世界"
        tokens = counter.count_text(text)
        import math

        assert tokens == math.ceil(len(text) / 4)

    def test_count_messages_estimate(self):
        counter = TokenCounter("some-unknown-model-xyz")
        messages = [
            {"role": "user", "content": "Hello"},
        ]
        tokens = counter.count_messages(messages)
        assert tokens > 0

    def test_count_text_empty_estimate(self):
        counter = TokenCounter("some-unknown-model-xyz")
        assert counter.count_text("") == 0


# ---------------------------------------------------------------------------
# BudgetTracker tests
# ---------------------------------------------------------------------------


class TestBudgetTracker:
    """BudgetTracker threshold and status tests."""

    def _make_settings(self, **overrides) -> CompactionSettings:
        defaults = {
            "context_limit": 10_000,
            "warn_ratio": 0.80,
            "compact_ratio": 0.90,
            "reserved_output_tokens": 1000,
            "safety_margin_tokens": 500,
        }
        defaults.update(overrides)
        return CompactionSettings(**defaults)

    def test_ok_status(self):
        settings = self._make_settings()
        tracker = BudgetTracker(settings, "gpt-4o-mini")
        # usable = 10000 - 1000 - 500 = 8500
        # warn = 8500 * 0.80 = 6800
        status = tracker.check(1000)
        assert status.status == "ok"
        assert status.current_tokens == 1000
        assert status.usable_budget == 8500
        assert status.warn_threshold == 6800
        assert status.compact_threshold == 7650  # 8500 * 0.90

    def test_warn_status(self):
        settings = self._make_settings()
        tracker = BudgetTracker(settings, "gpt-4o-mini")
        # warn = 6800, compact = 7650
        status = tracker.check(7000)
        assert status.status == "warn"

    def test_warn_boundary_exact(self):
        settings = self._make_settings()
        tracker = BudgetTracker(settings, "gpt-4o-mini")
        # Exactly at warn threshold
        status = tracker.check(6800)
        assert status.status == "warn"

    def test_compact_needed_status(self):
        settings = self._make_settings()
        tracker = BudgetTracker(settings, "gpt-4o-mini")
        status = tracker.check(8000)
        assert status.status == "compact_needed"

    def test_compact_boundary_exact(self):
        settings = self._make_settings()
        tracker = BudgetTracker(settings, "gpt-4o-mini")
        # Exactly at compact threshold
        status = tracker.check(7650)
        assert status.status == "compact_needed"

    def test_budget_status_fields_complete(self):
        settings = self._make_settings()
        tracker = BudgetTracker(settings, "gpt-4o-mini")
        status = tracker.check(5000)
        assert isinstance(status, BudgetStatus)
        assert isinstance(status.status, str)
        assert isinstance(status.current_tokens, int)
        assert isinstance(status.usable_budget, int)
        assert isinstance(status.warn_threshold, int)
        assert isinstance(status.compact_threshold, int)
        assert status.tokenizer_mode in ("exact", "estimate")

    def test_tokenizer_mode_propagated_exact(self):
        settings = self._make_settings()
        tracker = BudgetTracker(settings, "gpt-4o-mini")
        status = tracker.check(100)
        assert status.tokenizer_mode == "exact"

    def test_tokenizer_mode_propagated_estimate(self):
        settings = self._make_settings()
        tracker = BudgetTracker(settings, "unknown-model-xyz")
        status = tracker.check(100)
        assert status.tokenizer_mode == "estimate"

    def test_counter_property(self):
        settings = self._make_settings()
        tracker = BudgetTracker(settings, "gpt-4o-mini")
        assert tracker.counter is not None
        assert tracker.counter.tokenizer_mode == "exact"


# ---------------------------------------------------------------------------
# CompactionSettings validation tests
# ---------------------------------------------------------------------------


class TestCompactionSettings:
    """CompactionSettings validation rules."""

    def test_default_values(self):
        settings = CompactionSettings()
        assert settings.context_limit == 128_000
        assert settings.warn_ratio == 0.80
        assert settings.compact_ratio == 0.90
        assert settings.reserved_output_tokens == 2048
        assert settings.safety_margin_tokens == 1024

    def test_valid_custom_settings(self):
        settings = CompactionSettings(
            context_limit=50_000,
            warn_ratio=0.70,
            compact_ratio=0.85,
            reserved_output_tokens=1000,
            safety_margin_tokens=500,
        )
        assert settings.context_limit == 50_000
        assert settings.warn_ratio == 0.70

    def test_warn_ratio_must_be_less_than_compact_ratio(self):
        with pytest.raises(ValueError, match="warn_ratio.*must be less than.*compact_ratio"):
            CompactionSettings(warn_ratio=0.90, compact_ratio=0.80)

    def test_warn_ratio_equal_to_compact_ratio_rejected(self):
        with pytest.raises(ValueError, match="warn_ratio.*must be less than.*compact_ratio"):
            CompactionSettings(warn_ratio=0.85, compact_ratio=0.85)

    def test_warn_ratio_zero_rejected(self):
        with pytest.raises(ValueError, match="warn_ratio must be in"):
            CompactionSettings(warn_ratio=0.0, compact_ratio=0.90)

    def test_warn_ratio_negative_rejected(self):
        with pytest.raises(ValueError, match="warn_ratio must be in"):
            CompactionSettings(warn_ratio=-0.1, compact_ratio=0.90)

    def test_compact_ratio_one_rejected(self):
        with pytest.raises(ValueError, match="compact_ratio must be in"):
            CompactionSettings(warn_ratio=0.80, compact_ratio=1.0)

    def test_usable_budget_must_be_positive(self):
        with pytest.raises(ValueError, match="usable_input_budget must be > 0"):
            CompactionSettings(
                context_limit=1000,
                reserved_output_tokens=800,
                safety_margin_tokens=300,
            )
