"""Tests for memory flush candidate generation (Phase 2).

Covers:
- MemoryFlushGenerator: candidate extraction, tag classification, confidence range
- Candidate structure alignment with m2_architecture.md 3.3
- Edge cases: empty input, max candidates, max text bytes
"""

from __future__ import annotations

from src.agent.compaction import Turn
from src.agent.memory_flush import MemoryFlushGenerator
from src.config.settings import CompactionSettings
from src.session.manager import MessageWithSeq

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _msg(seq: int, role: str, content: str = "test") -> MessageWithSeq:
    return MessageWithSeq(seq=seq, role=role, content=content, tool_calls=None, tool_call_id=None)


def _make_turn(seq: int, user_content: str, assistant_content: str = "ok") -> Turn:
    return Turn(
        start_seq=seq,
        end_seq=seq + 1,
        messages=[
            _msg(seq, "user", user_content),
            _msg(seq + 1, "assistant", assistant_content),
        ],
    )


def _make_settings(**overrides) -> CompactionSettings:
    defaults = {
        "context_limit": 10_000,
        "warn_ratio": 0.70,
        "compact_ratio": 0.85,
        "reserved_output_tokens": 500,
        "safety_margin_tokens": 200,
        "max_flush_candidates": 20,
        "max_candidate_text_bytes": 2048,
    }
    defaults.update(overrides)
    return CompactionSettings(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMemoryFlushGenerator:

    def test_empty_turns(self):
        gen = MemoryFlushGenerator(_make_settings())
        result = gen.generate([], "main")
        assert result == []

    def test_explicit_user_preference_high_confidence(self):
        gen = MemoryFlushGenerator(_make_settings())
        turns = [_make_turn(0, "我喜欢用 Python 写代码，请记住这一点")]
        result = gen.generate(turns, "main")
        assert len(result) == 1
        assert result[0].confidence >= 0.8
        assert "user_preference" in result[0].constraint_tags

    def test_explicit_english_preference(self):
        gen = MemoryFlushGenerator(_make_settings())
        turns = [_make_turn(0, "Remember that I always prefer dark mode")]
        result = gen.generate(turns, "main")
        assert len(result) == 1
        assert result[0].confidence >= 0.8

    def test_safety_boundary_tag(self):
        gen = MemoryFlushGenerator(_make_settings())
        turns = [_make_turn(0, "永远不要删除我的文件")]
        result = gen.generate(turns, "main")
        assert len(result) == 1
        assert "safety_boundary" in result[0].constraint_tags
        assert "user_preference" in result[0].constraint_tags

    def test_decision_medium_confidence(self):
        gen = MemoryFlushGenerator(_make_settings())
        turns = [_make_turn(0, "我们决定使用 PostgreSQL 作为数据库")]
        result = gen.generate(turns, "main")
        assert len(result) == 1
        assert 0.5 <= result[0].confidence <= 0.7
        assert "fact" in result[0].constraint_tags

    def test_casual_acknowledgment_skipped(self):
        gen = MemoryFlushGenerator(_make_settings())
        turns = [_make_turn(0, "ok")]
        result = gen.generate(turns, "main")
        assert len(result) == 0

    def test_casual_thanks_skipped(self):
        gen = MemoryFlushGenerator(_make_settings())
        turns = [_make_turn(0, "谢谢")]
        result = gen.generate(turns, "main")
        assert len(result) == 0

    def test_general_conversation_low_confidence(self):
        gen = MemoryFlushGenerator(_make_settings())
        turns = [_make_turn(0, "这个功能的实现方式看起来不错，我需要继续研究一下")]
        result = gen.generate(turns, "main")
        assert len(result) == 1
        assert 0.2 <= result[0].confidence <= 0.4

    def test_confidence_in_range(self):
        gen = MemoryFlushGenerator(_make_settings())
        turns = [
            _make_turn(0, "请记住我喜欢 TypeScript"),
            _make_turn(2, "我们决定用 React"),
            _make_turn(4, "这个方案看起来比较复杂需要进一步分析讨论"),
        ]
        result = gen.generate(turns, "main")
        for c in result:
            assert 0.0 <= c.confidence <= 1.0

    def test_max_candidates_limit(self):
        gen = MemoryFlushGenerator(_make_settings(max_flush_candidates=3))
        turns = [_make_turn(i * 2, f"请记住规则 {i}") for i in range(10)]
        result = gen.generate(turns, "main")
        assert len(result) <= 3

    def test_max_text_bytes_truncation(self):
        gen = MemoryFlushGenerator(_make_settings(max_candidate_text_bytes=20))
        turns = [_make_turn(0, "请记住" + "a" * 100)]
        result = gen.generate(turns, "main")
        assert len(result) == 1
        assert len(result[0].candidate_text) <= 20

    def test_candidate_structure_complete(self):
        """All fields from m2_architecture.md 3.3 must be present."""
        gen = MemoryFlushGenerator(_make_settings())
        turns = [_make_turn(0, "记住我的名字是 Alice")]
        result = gen.generate(turns, "main")
        assert len(result) == 1
        c = result[0]
        assert isinstance(c.candidate_id, str) and len(c.candidate_id) > 0
        assert c.source_session_id == "main"
        assert isinstance(c.source_message_ids, list) and len(c.source_message_ids) > 0
        assert isinstance(c.candidate_text, str) and len(c.candidate_text) > 0
        assert isinstance(c.constraint_tags, list)
        assert isinstance(c.confidence, float)
        assert isinstance(c.created_at, str) and len(c.created_at) > 0

    def test_session_id_propagated(self):
        gen = MemoryFlushGenerator(_make_settings())
        turns = [_make_turn(0, "记住这个配置")]
        result = gen.generate(turns, "group:test-group")
        assert len(result) == 1
        assert result[0].source_session_id == "group:test-group"

    def test_only_user_messages_extracted(self):
        """Only user messages should be analyzed, not assistant responses."""
        gen = MemoryFlushGenerator(_make_settings())
        # Turn with casual user message but detailed assistant
        turn = Turn(
            start_seq=0,
            end_seq=1,
            messages=[
                _msg(0, "user", "ok"),  # Should be skipped
                _msg(1, "assistant", "记住这个非常重要的信息"),  # Should not be extracted
            ],
        )
        result = gen.generate([turn], "main")
        assert len(result) == 0
