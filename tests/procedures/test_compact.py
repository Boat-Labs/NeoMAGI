"""Tests for P2-M2b Slice G: purposeful compact."""

from __future__ import annotations

from src.procedures.compact import extract_task_state, render_task_state_text
from src.procedures.handoff import TaskStateSnapshot
from src.procedures.types import ActiveProcedure


def _make_active(**ctx_overrides) -> ActiveProcedure:
    return ActiveProcedure(
        instance_id="inst-1",
        session_id="sess-1",
        spec_id="test.spec",
        spec_version=1,
        state="working",
        context=ctx_overrides,
        revision=1,
    )


class TestExtractTaskState:
    def test_all_keys_present(self):
        active = _make_active(
            _objectives=["goal1", "goal2"],
            _todos=["todo1"],
            _blockers=["blocker1"],
            _last_result={"answer": 42},
            _pending=["approval1"],
        )
        snap = extract_task_state(active)
        assert snap.objectives == ("goal1", "goal2")
        assert snap.todos == ("todo1",)
        assert snap.blockers == ("blocker1",)
        assert snap.last_valid_result == {"answer": 42}
        assert snap.pending_approvals == ("approval1",)

    def test_missing_keys_return_empty(self):
        active = _make_active()
        snap = extract_task_state(active)
        assert snap.objectives == ()
        assert snap.todos == ()
        assert snap.blockers == ()
        assert snap.last_valid_result == {}
        assert snap.pending_approvals == ()

    def test_scalar_converted_to_tuple(self):
        active = _make_active(_objectives="single goal")
        snap = extract_task_state(active)
        assert snap.objectives == ("single goal",)


class TestRenderTaskStateText:
    def test_render_with_content(self):
        snap = TaskStateSnapshot(
            objectives=("goal1",),
            todos=("todo1", "todo2"),
            blockers=(),
        )
        text = render_task_state_text(snap)
        assert "Objectives: goal1" in text
        assert "TODOs: todo1; todo2" in text
        assert "Blockers" not in text  # empty, not rendered

    def test_render_empty_snapshot(self):
        snap = TaskStateSnapshot()
        text = render_task_state_text(snap)
        assert text == ""
