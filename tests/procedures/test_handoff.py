"""Tests for P2-M2b Slice B: handoff packet types and builder."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.procedures.handoff import (
    MAX_ITEM_CHARS,
    MAX_PACKET_BYTES,
    MAX_TASK_BRIEF_CHARS,
    HandoffPacket,
    HandoffPacketBuilder,
    ReviewResult,
    TaskStateSnapshot,
    WorkerResult,
)
from src.procedures.roles import AgentRole
from src.procedures.types import ActiveProcedure, ProcedureExecutionMetadata


def _make_active(**overrides) -> ActiveProcedure:
    defaults = dict(
        instance_id="inst-1",
        session_id="sess-1",
        spec_id="test.spec",
        spec_version=1,
        state="planning",
        context={"key_a": "val_a", "key_b": "val_b", "_private": "x"},
        revision=0,
    )
    defaults.update(overrides)
    return ActiveProcedure(**defaults)


# ---------------------------------------------------------------------------
# HandoffPacket
# ---------------------------------------------------------------------------


class TestHandoffPacket:
    def test_frozen(self):
        pkt = HandoffPacket(
            handoff_id="h1",
            source_actor=AgentRole.primary,
            target_role=AgentRole.worker,
            task_brief="do something",
        )
        with pytest.raises(Exception):
            pkt.task_brief = "other"  # type: ignore[misc]

    def test_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            HandoffPacket(
                handoff_id="h1",
                source_actor=AgentRole.primary,
                target_role=AgentRole.worker,
                task_brief="do something",
                unknown_field="bad",  # type: ignore[call-arg]
            )

    def test_rejects_empty_brief(self):
        with pytest.raises(ValidationError, match="task_brief must not be empty"):
            HandoffPacket(
                handoff_id="h1",
                source_actor=AgentRole.primary,
                target_role=AgentRole.worker,
                task_brief="   ",
            )


# ---------------------------------------------------------------------------
# WorkerResult / ReviewResult / TaskStateSnapshot
# ---------------------------------------------------------------------------


class TestResultTypes:
    def test_worker_result_defaults(self):
        r = WorkerResult(ok=True)
        assert r.result == {}
        assert r.iterations_used == 0
        assert r.error_code == ""

    def test_review_result_defaults(self):
        r = ReviewResult(approved=False)
        assert r.concerns == ()
        assert r.suggestions == ()

    def test_task_state_snapshot_defaults(self):
        s = TaskStateSnapshot()
        assert s.objectives == ()
        assert s.todos == ()
        assert s.last_valid_result == {}


# ---------------------------------------------------------------------------
# HandoffPacketBuilder
# ---------------------------------------------------------------------------


class TestHandoffPacketBuilder:
    def test_include_keys_filter(self):
        active = _make_active()
        builder = HandoffPacketBuilder(include_keys=("key_a",))
        pkt = builder.build(
            active=active,
            spec=None,  # type: ignore[arg-type]
            target_role=AgentRole.worker,
            task_brief="do task",
        )
        assert pkt.current_state == {"key_a": "val_a"}
        assert "key_b" not in pkt.current_state
        assert "_private" not in pkt.current_state

    def test_auto_fills_handoff_id(self):
        active = _make_active()
        builder = HandoffPacketBuilder()
        pkt = builder.build(
            active=active,
            spec=None,  # type: ignore[arg-type]
            target_role=AgentRole.worker,
            task_brief="do task",
        )
        assert pkt.handoff_id  # non-empty
        assert pkt.source_actor == AgentRole.primary

    def test_propagates_execution_metadata(self):
        meta = ProcedureExecutionMetadata(actor="primary")
        active = _make_active(execution_metadata=meta)
        builder = HandoffPacketBuilder()
        pkt = builder.build(
            active=active,
            spec=None,  # type: ignore[arg-type]
            target_role=AgentRole.worker,
            task_brief="do task",
        )
        assert pkt.execution_metadata.actor == "primary"

    def test_rejects_oversized_task_brief(self):
        active = _make_active()
        builder = HandoffPacketBuilder()
        with pytest.raises(ValueError, match="task_brief exceeds"):
            builder.build(
                active=active,
                spec=None,  # type: ignore[arg-type]
                target_role=AgentRole.worker,
                task_brief="x" * (MAX_TASK_BRIEF_CHARS + 1),
            )

    def test_rejects_oversized_constraint_item(self):
        active = _make_active()
        builder = HandoffPacketBuilder()
        with pytest.raises(ValueError, match="constraints"):
            builder.build(
                active=active,
                spec=None,  # type: ignore[arg-type]
                target_role=AgentRole.worker,
                task_brief="do task",
                constraints=("x" * (MAX_ITEM_CHARS + 1),),
            )

    def test_rejects_oversized_total_packet(self):
        active = _make_active()
        builder = HandoffPacketBuilder()
        # Many evidence items to exceed 32KB
        big_evidence = tuple("e" * MAX_ITEM_CHARS for _ in range(100))
        with pytest.raises(ValueError, match="exceeds.*bytes"):
            builder.build(
                active=active,
                spec=None,  # type: ignore[arg-type]
                target_role=AgentRole.worker,
                task_brief="do task",
                evidence=big_evidence,
            )

    def test_empty_include_keys_gives_empty_state(self):
        active = _make_active()
        builder = HandoffPacketBuilder(include_keys=())
        pkt = builder.build(
            active=active,
            spec=None,  # type: ignore[arg-type]
            target_role=AgentRole.worker,
            task_brief="do task",
        )
        assert pkt.current_state == {}
