"""Unit tests for src.skills.task_frame — rule-based TaskFrame extraction (P2-M1b-P2).

Validates:
- Chinese keyword → correct TaskType
- English keyword → correct TaskType
- High-risk signal detection
- Unknown fallback for ambiguous content
- Chat fallback for short questions
- target_outcome truncation at 200 chars
- Empty/whitespace content → unknown
"""

from __future__ import annotations

import pytest

from src.skills.task_frame import extract_task_frame
from src.skills.types import TaskType

# ---------------------------------------------------------------------------
# Chinese keyword classification
# ---------------------------------------------------------------------------


class TestChineseKeywords:
    @pytest.mark.parametrize(
        "keyword,expected",
        [
            ("请搜索相关资料", TaskType.research),
            ("帮我查找文件", TaskType.research),
            ("调研一下这个方案", TaskType.research),
            ("分析这段代码", TaskType.research),
            ("写一个函数", TaskType.create),
            ("创建新模块", TaskType.create),
            ("帮我起草文档", TaskType.create),
            ("修改这个函数", TaskType.edit),
            ("重写这段逻辑", TaskType.edit),
            ("更新配置文件", TaskType.edit),
            ("报错了怎么办", TaskType.debug),
            ("修复这个问题", TaskType.debug),
            ("排查一下日志", TaskType.debug),
        ],
    )
    def test_chinese_keyword_classification(self, keyword: str, expected: TaskType) -> None:
        frame = extract_task_frame(keyword)
        assert frame.task_type == expected


# ---------------------------------------------------------------------------
# English keyword classification
# ---------------------------------------------------------------------------


class TestEnglishKeywords:
    @pytest.mark.parametrize(
        "keyword,expected",
        [
            ("search for related files", TaskType.research),
            ("find the configuration", TaskType.research),
            ("research this topic", TaskType.research),
            ("analyze the error logs", TaskType.research),
            ("write a test case", TaskType.create),
            ("create a new module", TaskType.create),
            ("draft a design doc", TaskType.create),
            ("edit the function", TaskType.edit),
            ("modify the schema", TaskType.edit),
            ("update the README", TaskType.edit),
            ("refactor the code", TaskType.edit),
            ("debug the crash", TaskType.debug),
            ("fix the bug", TaskType.debug),
            ("there is an error in the code", TaskType.debug),
            ("this bug needs fixing", TaskType.debug),
        ],
    )
    def test_english_keyword_classification(self, keyword: str, expected: TaskType) -> None:
        frame = extract_task_frame(keyword)
        assert frame.task_type == expected


# ---------------------------------------------------------------------------
# Risk detection
# ---------------------------------------------------------------------------


class TestRiskDetection:
    @pytest.mark.parametrize(
        "content",
        [
            "删除这个文件",
            "DROP TABLE users",
            "rm -rf /tmp/data",
            "git push --force origin main",
            "git reset --hard HEAD",
        ],
    )
    def test_high_risk_detected(self, content: str) -> None:
        frame = extract_task_frame(content)
        assert frame.risk == "high"

    def test_low_risk_default(self) -> None:
        frame = extract_task_frame("请帮我写一个函数")
        assert frame.risk == "low"

    def test_rm_without_space_is_not_high_risk(self) -> None:
        """'rm' must be followed by whitespace to trigger high risk."""
        frame = extract_task_frame("the form element is primary")
        assert frame.risk == "low"


# ---------------------------------------------------------------------------
# Unknown / Chat fallback
# ---------------------------------------------------------------------------


class TestFallback:
    def test_empty_content_is_unknown(self) -> None:
        frame = extract_task_frame("")
        assert frame.task_type == TaskType.unknown

    def test_whitespace_only_is_unknown(self) -> None:
        frame = extract_task_frame("   \n\t  ")
        assert frame.task_type == TaskType.unknown

    def test_ambiguous_content_is_unknown(self) -> None:
        frame = extract_task_frame(
            "The system architecture follows a layered pattern with clear boundaries."
        )
        assert frame.task_type == TaskType.unknown

    def test_short_question_is_chat(self) -> None:
        frame = extract_task_frame("你好吗？")
        assert frame.task_type == TaskType.chat

    def test_short_english_question_is_chat(self) -> None:
        frame = extract_task_frame("How are you?")
        assert frame.task_type == TaskType.chat


# ---------------------------------------------------------------------------
# target_outcome truncation
# ---------------------------------------------------------------------------


class TestTargetOutcome:
    def test_short_content_kept_as_is(self) -> None:
        frame = extract_task_frame("hello world")
        assert frame.target_outcome == "hello world"

    def test_truncation_at_200_chars(self) -> None:
        long_content = "x" * 500
        frame = extract_task_frame(long_content)
        assert frame.target_outcome is not None
        assert len(frame.target_outcome) == 200

    def test_empty_content_yields_none(self) -> None:
        frame = extract_task_frame("")
        assert frame.target_outcome is None


# ---------------------------------------------------------------------------
# Passthrough parameters
# ---------------------------------------------------------------------------


class TestPassthrough:
    def test_mode_passthrough(self) -> None:
        frame = extract_task_frame("hello", mode="agent_mode")
        assert frame.current_mode == "agent_mode"

    def test_channel_passthrough(self) -> None:
        frame = extract_task_frame("hello", channel="telegram")
        assert frame.channel == "telegram"

    def test_available_tools_passthrough(self) -> None:
        tools = ("read_file", "write_file")
        frame = extract_task_frame("hello", available_tools=tools)
        assert frame.available_tools == tools

    def test_current_procedure_always_none(self) -> None:
        """V1: current_procedure is always None."""
        frame = extract_task_frame("any content")
        assert frame.current_procedure is None

    def test_default_mode(self) -> None:
        frame = extract_task_frame("hello")
        assert frame.current_mode == "chat_safe"
