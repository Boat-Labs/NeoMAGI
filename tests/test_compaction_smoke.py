"""End-to-end smoke test for Phase 2 compaction pipeline.

Verifies: 30+ turns -> compact -> structure complete + watermark advances +
anchor preserved + flush candidates present.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.compaction import CompactionEngine
from src.agent.token_budget import BudgetStatus, TokenCounter
from src.config.settings import CompactionSettings
from src.session.manager import MessageWithSeq


def _msg(seq: int, role: str, content: str = "test") -> MessageWithSeq:
    return MessageWithSeq(seq=seq, role=role, content=content, tool_calls=None, tool_call_id=None)


def _make_long_history(n_turns: int = 30) -> list[MessageWithSeq]:
    msgs = []
    seq = 0
    for i in range(n_turns):
        if i % 5 == 0:
            # Every 5th turn has explicit preference
            msgs.append(_msg(seq, "user", f"请记住我偏好方案 {i}"))
        elif i % 3 == 0:
            msgs.append(_msg(seq, "user", f"我们决定采用策略 {i}"))
        else:
            msgs.append(_msg(seq, "user", f"这是第 {i} 轮对话的详细问题内容"))
        seq += 1
        msgs.append(_msg(seq, "assistant", f"好的，这是第 {i} 轮的回复"))
        seq += 1
    return msgs


@pytest.mark.asyncio
class TestCompactionSmoke:

    async def test_full_pipeline_30_turns(self):
        """30+ turns -> compact -> verify all outputs."""
        summary_response = (
            '{"facts":["fact1","fact2"],'
            '"decisions":["decision1"],'
            '"open_todos":["todo1"],'
            '"user_prefs":["pref1"],'
            '"timeline":["event1"]}'
        )

        model_client = MagicMock()
        model_client.chat = AsyncMock(return_value=summary_response)

        settings = CompactionSettings(
            context_limit=10_000,
            warn_ratio=0.70,
            compact_ratio=0.85,
            reserved_output_tokens=500,
            safety_margin_tokens=200,
            min_preserved_turns=8,
        )
        counter = TokenCounter("gpt-4o-mini")
        engine = CompactionEngine(model_client, counter, settings)

        msgs = _make_long_history(30)
        budget_status = BudgetStatus(
            status="compact_needed",
            current_tokens=9000,
            usable_budget=9300,
            warn_threshold=6510,
            compact_threshold=7905,
            tokenizer_mode="exact",
        )

        result = await engine.compact(
            messages=msgs,
            system_prompt="You are Magi, a personal AI assistant. " * 10,
            tools_schema=[],
            budget_status=budget_status,
            last_compaction_seq=None,
            previous_compacted_context=None,
            current_user_seq=100,  # All turns are completed
            model="gpt-4o-mini",
            session_id="smoke-test",
        )

        # Structure complete
        assert result.status == "success"
        assert result.compacted_context is not None
        assert len(result.compacted_context) > 0

        # Watermark advanced
        assert result.new_compaction_seq > 0

        # Metadata complete (ADR 0028)
        meta = result.compaction_metadata
        assert meta["schema_version"] == 1
        assert meta["status"] == "success"
        assert meta["preserved_count"] == 8  # min_preserved_turns
        assert meta["summarized_count"] > 0
        assert meta["anchor_validation_passed"] is True

        # Flush candidates present (some turns had explicit preferences)
        assert len(result.memory_flush_candidates) > 0
        for c in result.memory_flush_candidates:
            assert 0.0 <= c.confidence <= 1.0
            assert len(c.candidate_id) > 0
            assert c.source_session_id == "smoke-test"

    async def test_second_compaction_advances_watermark(self):
        """Rolling compaction: second call advances watermark further."""
        model_client = MagicMock()
        model_client.chat = AsyncMock(
            return_value='{"facts":[],"decisions":[],"open_todos":[],"user_prefs":[],"timeline":[]}'
        )

        settings = CompactionSettings(
            context_limit=10_000,
            warn_ratio=0.70,
            compact_ratio=0.85,
            reserved_output_tokens=500,
            safety_margin_tokens=200,
            min_preserved_turns=3,
        )
        counter = TokenCounter("gpt-4o-mini")
        engine = CompactionEngine(model_client, counter, settings)

        # First compaction
        msgs1 = _make_long_history(10)
        result1 = await engine.compact(
            messages=msgs1,
            system_prompt=(
                "System prompt content for validation purposes"
                " - this is long enough to pass the check"
            ),
            tools_schema=[],
            budget_status=BudgetStatus("compact_needed", 9000, 9300, 6510, 7905, "exact"),
            last_compaction_seq=None,
            previous_compacted_context=None,
            current_user_seq=100,
            model="gpt-4o-mini",
        )
        assert result1.status == "success"
        seq1 = result1.new_compaction_seq

        # Add more messages and compact again
        msgs2 = msgs1 + _make_long_history(10)
        # Fix seq for the second batch
        max_seq = max(m.seq for m in msgs1) + 1
        for i, m in enumerate(msgs2[len(msgs1):]):
            msgs2[len(msgs1) + i] = MessageWithSeq(
                seq=max_seq + i,
                role=m.role,
                content=m.content,
                tool_calls=None,
                tool_call_id=None,
            )

        result2 = await engine.compact(
            messages=msgs2,
            system_prompt=(
                "System prompt content for validation purposes"
                " - this is long enough to pass the check"
            ),
            tools_schema=[],
            budget_status=BudgetStatus("compact_needed", 9000, 9300, 6510, 7905, "exact"),
            last_compaction_seq=seq1,
            previous_compacted_context=result1.compacted_context,
            current_user_seq=200,
            model="gpt-4o-mini",
        )
        assert result2.status == "success"
        assert result2.new_compaction_seq > seq1  # Watermark advanced
