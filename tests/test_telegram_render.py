"""Tests for Telegram response rendering: splitting, formatting, error mapping."""

from __future__ import annotations

import pytest

from src.channels.telegram_render import (
    format_for_telegram,
    friendly_error_message,
    split_message,
)


# ── split_message ────────────────────────────────────────────────────────────


class TestSplitMessage:
    def test_empty_returns_empty(self):
        assert split_message("") == []

    def test_short_text_single_chunk(self):
        assert split_message("hello") == ["hello"]

    def test_exact_limit_single_chunk(self):
        text = "A" * 4096
        assert split_message(text, max_length=4096) == [text]

    def test_splits_on_paragraph_boundary(self):
        part_a = "A" * 3000
        part_b = "B" * 3000
        text = part_a + "\n\n" + part_b
        parts = split_message(text, max_length=4096)
        assert len(parts) == 2
        assert parts[0] == part_a
        assert parts[1] == part_b

    def test_splits_on_sentence_boundary(self):
        # ~13 chars per sentence × 400 = ~5200 chars
        text = ("Hello world. " * 400).strip()
        parts = split_message(text, max_length=4096)
        assert len(parts) >= 2
        assert all(len(p) <= 4096 for p in parts)

    def test_hard_cut_no_boundaries(self):
        text = "A" * 8192
        parts = split_message(text, max_length=4096)
        assert len(parts) == 2
        assert all(len(p) <= 4096 for p in parts)
        assert "".join(parts) == text

    def test_preserves_small_code_block(self):
        code = "```python\nx = 1\ny = 2\n```"
        text = "Before\n\n" + code + "\n\nAfter"
        parts = split_message(text, max_length=4096)
        assert len(parts) == 1
        assert "```python" in parts[0]

    def test_code_block_kept_intact_across_split(self):
        before = "A" * 3000
        code = "```python\nx = 1\n```"
        after = "B" * 3000
        text = before + "\n\n" + code + "\n\n" + after
        parts = split_message(text, max_length=4096)
        assert len(parts) >= 2
        # Code block should be in one piece
        code_part = [p for p in parts if "```python" in p]
        assert len(code_part) == 1
        assert "```" in code_part[0][code_part[0].index("```python") + 3 :]

    def test_oversized_code_block_splits_with_fences(self):
        code = "```python\n" + "x = 1\n" * 1000 + "```"
        parts = split_message(code, max_length=4096)
        assert len(parts) > 1
        assert all(len(p) <= 4096 for p in parts)
        for p in parts:
            assert p.startswith("```python")
            assert p.rstrip().endswith("```")

    def test_all_chunks_within_limit(self):
        text = "A" * 20000
        parts = split_message(text, max_length=4096)
        assert all(len(p) <= 4096 for p in parts)
        assert "".join(parts) == text

    def test_chinese_sentence_splitting(self):
        # Chinese sentences end with 。
        text = ("这是一个测试句子。" * 600).strip()
        parts = split_message(text, max_length=4096)
        assert len(parts) >= 2
        assert all(len(p) <= 4096 for p in parts)

    def test_custom_max_length(self):
        text = "Hello world. This is a test."
        parts = split_message(text, max_length=15)
        assert all(len(p) <= 15 for p in parts)


# ── format_for_telegram ──────────────────────────────────────────────────────


class TestFormatForTelegram:
    def test_empty_text(self):
        text, mode = format_for_telegram("")
        assert text == ""
        assert mode is None

    def test_plain_text_no_markdown(self):
        text, mode = format_for_telegram("Hello world")
        assert mode is None
        assert text == "Hello world"

    def test_plain_text_with_special_chars(self):
        """No markdown patterns → plain text, no escaping."""
        text, mode = format_for_telegram("Price is 10.5! Check [here].")
        assert mode is None
        assert text == "Price is 10.5! Check [here]."

    def test_code_block_triggers_markdownv2(self):
        text = "Hello\n```python\nprint('hi')\n```"
        formatted, mode = format_for_telegram(text)
        assert mode == "MarkdownV2"
        assert "```python" in formatted

    def test_inline_code_triggers_markdownv2(self):
        text = "Use `pip install` to install"
        formatted, mode = format_for_telegram(text)
        assert mode == "MarkdownV2"
        assert "`pip install`" in formatted

    def test_bold_conversion(self):
        text = "This is **bold** text"
        formatted, mode = format_for_telegram(text)
        assert mode == "MarkdownV2"
        assert "*bold*" in formatted
        assert "**" not in formatted

    def test_bold_with_special_chars(self):
        text = "This is **bold.text!** end"
        formatted, mode = format_for_telegram(text)
        assert mode == "MarkdownV2"
        assert "*bold\\.text\\!*" in formatted

    def test_escapes_special_chars_outside_code(self):
        text = "Price: 10.5! **bold** `code`"
        formatted, mode = format_for_telegram(text)
        assert mode == "MarkdownV2"
        # Period and exclamation mark should be escaped outside code/bold
        assert "10\\.5\\!" in formatted
        # Code should be preserved
        assert "`code`" in formatted

    def test_code_block_content_preserved(self):
        text = "```python\nif x > 0:\n    print(x)\n```"
        formatted, mode = format_for_telegram(text)
        assert mode == "MarkdownV2"
        # Content inside code block should not have MarkdownV2 escaping (except \ and `)
        assert "print" in formatted

    def test_mixed_code_and_text(self):
        text = "Use this:\n```bash\ncurl http://example.com\n```\nDone."
        formatted, mode = format_for_telegram(text)
        assert mode == "MarkdownV2"
        assert "```bash" in formatted


# ── friendly_error_message ───────────────────────────────────────────────────


class TestFriendlyErrorMessage:
    def test_session_busy(self):
        assert friendly_error_message("SESSION_BUSY") == "当前正在处理中，请稍后重试"

    def test_budget_exceeded(self):
        assert friendly_error_message("BUDGET_EXCEEDED") == "预算额度已用完"

    def test_provider_not_available(self):
        assert friendly_error_message("PROVIDER_NOT_AVAILABLE") == "模型服务暂不可用"

    def test_unknown_code(self):
        msg = friendly_error_message("UNKNOWN_CODE")
        assert "处理消息时遇到了问题" in msg

    def test_none_code(self):
        msg = friendly_error_message(None)
        assert "处理消息时遇到了问题" in msg
