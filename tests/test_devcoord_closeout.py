from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.devcoord_helpers import (
    CoordError,
    CoordService,
    FakeClock,
    MemoryCoordStore,
    init_git_repo_with_review,
    make_paths,
)


def test_full_flow_renders_projection_files(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    clock = FakeClock(
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:10:00Z",
        "2026-03-01T10:15:00Z",
    )
    service = CoordService(paths=paths, store=store, now_fn=clock)

    service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
    service.open_gate(
        "M7",
        phase="1",
        gate_id="G-M7-P1",
        allowed_role="backend",
        target_commit="abc1234",
        task="open backend phase 1 gate",
    )
    service.ack(
        "M7",
        role="backend",
        command="GATE_OPEN",
        gate_id="G-M7-P1",
        commit="abc1234",
        task="ACK GATE_OPEN, starting Phase 1",
    )
    service.heartbeat(
        "M7",
        role="backend",
        phase="1",
        status="working",
        task="running implementation",
        eta_min=25,
        gate_id="G-M7-P1",
        target_commit="abc1234",
        branch="feat/backend-m7-control-plane",
    )
    service.phase_complete(
        "M7",
        role="backend",
        phase="1",
        gate_id="G-M7-P1",
        commit="def5678",
        task="Phase 1 complete",
        branch="feat/backend-m7-control-plane",
    )
    service.render("M7")

    log_dir = paths.log_dir("m7", "2026-03-01")
    heartbeat_events = [
        json.loads(line)
        for line in (log_dir / "heartbeat_events.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    assert [event["event"] for event in heartbeat_events] == [
        "GATE_OPEN_SENT",
        "ACK",
        "GATE_EFFECTIVE",
        "HEARTBEAT",
        "PHASE_COMPLETE",
    ]
    assert [event["event_seq"] for event in heartbeat_events] == [1, 2, 3, 4, 5]
    assert heartbeat_events[1]["ack_of"] == "GATE_OPEN"
    assert heartbeat_events[3]["branch"] == "feat/backend-m7-control-plane"
    assert heartbeat_events[4]["target_commit"] == "def5678"

    gate_state = (log_dir / "gate_state.md").read_text("utf-8")
    assert "| G-M7-P1 | 1 | open |  | 2026-03-01T10:05:00Z |  | def5678 |  |" in gate_state

    watchdog_status = (log_dir / "watchdog_status.md").read_text("utf-8")
    assert (
        "| backend | done | 2026-03-01T10:15:00Z | Phase 1 complete | none | "
        "waiting for next gate after G-M7-P1 |"
    ) in watchdog_status
    assert "| tester | idle |  |  | none | awaiting gate |" in watchdog_status

    progress = paths.progress_file.read_text("utf-8")
    assert progress.count("<!-- devcoord:begin milestone=m7 -->") == 1
    assert "## 2026-03-01 (generated) | M7" in progress
    assert "- Status: in_progress" in progress
    assert "- Next: 继续推进 G-M7-P1，当前 allowed_role=backend" in progress


def test_phase_complete_is_idempotent_for_same_gate_commit(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    clock = FakeClock(
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:10:00Z",
        "2026-03-01T10:15:00Z",
    )
    service = CoordService(paths=paths, store=store, now_fn=clock)

    service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
    service.open_gate(
        "M7",
        phase="1",
        gate_id="G-M7-P1",
        allowed_role="backend",
        target_commit="abc1234",
        task="open backend phase 1 gate",
    )
    service.ack(
        "M7",
        role="backend",
        command="GATE_OPEN",
        gate_id="G-M7-P1",
        commit="abc1234",
        task="ACK GATE_OPEN, starting Phase 1",
    )
    service.phase_complete(
        "M7",
        role="backend",
        phase="1",
        gate_id="G-M7-P1",
        commit="def5678",
        task="Phase 1 complete",
        branch="feat/backend-m7-control-plane",
    )
    service.phase_complete(
        "M7",
        role="backend",
        phase="1",
        gate_id="G-M7-P1",
        commit="def5678",
        task="Phase 1 complete duplicate retry",
        branch="feat/backend-m7-control-plane",
    )
    service.render("M7")

    log_dir = paths.log_dir("m7", "2026-03-01")
    heartbeat_events = [
        json.loads(line)
        for line in (log_dir / "heartbeat_events.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    assert [event["event"] for event in heartbeat_events] == [
        "GATE_OPEN_SENT",
        "ACK",
        "GATE_EFFECTIVE",
        "PHASE_COMPLETE",
    ]
    assert heartbeat_events[-1]["target_commit"] == "def5678"


def test_gate_review_and_close_render_closed_state(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    report_path = "dev_docs/reviews/m7_phase1_2026-03-01.md"
    report_commit = init_git_repo_with_review(paths, report_path)
    clock = FakeClock(
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:10:00Z",
        "2026-03-01T10:15:00Z",
        "2026-03-01T10:20:00Z",
        "2026-03-01T10:25:00Z",
    )
    service = CoordService(paths=paths, store=store, now_fn=clock)

    service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
    service.open_gate(
        "M7",
        phase="1",
        gate_id="G-M7-P1",
        allowed_role="backend",
        target_commit="abc1234",
        task="open backend phase 1 gate",
    )
    service.ack(
        "M7",
        role="backend",
        command="GATE_OPEN",
        gate_id="G-M7-P1",
        commit="abc1234",
        task="ACK GATE_OPEN, starting Phase 1",
    )
    service.phase_complete(
        "M7",
        role="backend",
        phase="1",
        gate_id="G-M7-P1",
        commit="def5678",
        task="Phase 1 complete",
        branch="main",
    )
    service.gate_review(
        "M7",
        role="tester",
        phase="1",
        gate_id="G-M7-P1",
        result="PASS",
        report_commit=report_commit,
        report_path=report_path,
        task="Phase 1 review PASS",
    )
    service.render("M7")
    service.gate_close(
        "M7",
        phase="1",
        gate_id="G-M7-P1",
        result="PASS",
        report_commit=report_commit,
        report_path=report_path,
        task="close gate after review",
    )
    service.render("M7")

    log_dir = paths.log_dir("m7", "2026-03-01")
    heartbeat_events = [
        json.loads(line)
        for line in (log_dir / "heartbeat_events.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    assert [event["event"] for event in heartbeat_events] == [
        "GATE_OPEN_SENT",
        "ACK",
        "GATE_EFFECTIVE",
        "PHASE_COMPLETE",
        "GATE_REVIEW_COMPLETE",
        "GATE_CLOSE",
    ]
    assert heartbeat_events[-2]["result"] == "PASS"
    assert heartbeat_events[-1]["report_commit"] == report_commit

    gate_state = (log_dir / "gate_state.md").read_text("utf-8")
    assert (
        "| G-M7-P1 | 1 | closed | PASS | 2026-03-01T10:05:00Z | "
        "2026-03-01T10:20:00Z | def5678 | "
        f"{report_path} ({report_commit}) |"
    ) in gate_state

    watchdog_status = (log_dir / "watchdog_status.md").read_text("utf-8")
    assert (
        "| tester | done | 2026-03-01T10:15:00Z | Phase 1 review PASS | none | "
        "review submitted for G-M7-P1 |"
    ) in watchdog_status

    progress = paths.progress_file.read_text("utf-8")
    assert progress.count("<!-- devcoord:begin milestone=m7 -->") == 1
    assert "- Status: done" in progress
    assert f"`{report_path}` ({report_commit})" in progress


def test_gate_close_requires_rendered_reconciliation(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    report_path = "dev_docs/reviews/m7_phase1_2026-03-01.md"
    report_commit = init_git_repo_with_review(paths, report_path)
    service = CoordService(
        paths=paths,
        store=store,
        now_fn=FakeClock(
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:05:00Z",
            "2026-03-01T10:10:00Z",
            "2026-03-01T10:15:00Z",
        ),
    )

    service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
    service.open_gate(
        "M7",
        phase="1",
        gate_id="G-M7-P1",
        allowed_role="backend",
        target_commit="abc1234",
        task="open backend phase 1 gate",
    )
    service.ack(
        "M7",
        role="backend",
        command="GATE_OPEN",
        gate_id="G-M7-P1",
        commit="abc1234",
        task="ACK GATE_OPEN, starting Phase 1",
    )
    service.gate_review(
        "M7",
        role="tester",
        phase="1",
        gate_id="G-M7-P1",
        result="PASS",
        report_commit=report_commit,
        report_path=report_path,
        task="Phase 1 review PASS",
    )

    with pytest.raises(
        CoordError,
        match="cannot close gate before heartbeat_events.jsonl has been rendered",
    ):
        service.gate_close(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            result="PASS",
            report_commit=report_commit,
            report_path=report_path,
            task="close gate after review",
        )


def test_gate_close_requires_visible_report_commit(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    report_path = "dev_docs/reviews/m7_phase1_2026-03-01.md"
    init_git_repo_with_review(paths, report_path)
    service = CoordService(
        paths=paths,
        store=store,
        now_fn=FakeClock(
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:05:00Z",
            "2026-03-01T10:10:00Z",
            "2026-03-01T10:15:00Z",
        ),
    )

    service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
    service.open_gate(
        "M7",
        phase="1",
        gate_id="G-M7-P1",
        allowed_role="backend",
        target_commit="abc1234",
        task="open backend phase 1 gate",
    )
    service.ack(
        "M7",
        role="backend",
        command="GATE_OPEN",
        gate_id="G-M7-P1",
        commit="abc1234",
        task="ACK GATE_OPEN, starting Phase 1",
    )
    service.gate_review(
        "M7",
        role="tester",
        phase="1",
        gate_id="G-M7-P1",
        result="PASS",
        report_commit="deadbeef",
        report_path=report_path,
        task="Phase 1 review PASS",
    )
    service.render("M7")

    with pytest.raises(CoordError, match="git cat-file -e deadbeef"):
        service.gate_close(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            result="PASS",
            report_commit="deadbeef",
            report_path=report_path,
            task="close gate after review",
        )


def test_audit_ignores_pending_ack_for_closed_gate(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    report_path = "dev_docs/reviews/m7_phase1_2026-03-01.md"
    report_commit = init_git_repo_with_review(paths, report_path)
    service = CoordService(
        paths=paths,
        store=store,
        now_fn=FakeClock(
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:05:00Z",
            "2026-03-01T10:10:00Z",
            "2026-03-01T10:15:00Z",
        ),
    )

    service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
    service.open_gate(
        "M7",
        phase="1",
        gate_id="G-M7-P1",
        allowed_role="backend",
        target_commit="abc1234",
        task="open backend gate that stays unacked",
    )
    service.gate_review(
        "M7",
        role="pm",
        phase="1",
        gate_id="G-M7-P1",
        result="PASS",
        report_commit=report_commit,
        report_path=report_path,
        task="record blocked preflight review",
    )
    service.render("M7")
    service.gate_close(
        "M7",
        phase="1",
        gate_id="G-M7-P1",
        result="PASS",
        report_commit=report_commit,
        report_path=report_path,
        task="close blocked preflight gate",
    )
    service.render("M7")

    audit = service.audit("M7")
    assert audit["open_gates"] == []
    assert audit["pending_ack_messages"] == []


def test_milestone_close_requires_clean_audit(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    service = CoordService(
        paths=paths,
        store=store,
        now_fn=FakeClock("2026-03-01T10:01:00Z"),
    )

    service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
    service.open_gate(
        "M7",
        phase="1",
        gate_id="G-M7-P1",
        allowed_role="backend",
        target_commit="abc1234",
        task="open backend phase 1 gate",
    )
    service.render("M7")

    with pytest.raises(CoordError, match="cannot close milestone while gates remain open"):
        service.close_milestone("M7")


def test_milestone_close_closes_all_milestone_records(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    report_path = "dev_docs/reviews/m7_phase1_2026-03-01.md"
    report_commit = init_git_repo_with_review(paths, report_path)
    service = CoordService(
        paths=paths,
        store=store,
        now_fn=FakeClock(
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:05:00Z",
            "2026-03-01T10:10:00Z",
            "2026-03-01T10:15:00Z",
        ),
    )

    service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
    service.open_gate(
        "M7",
        phase="1",
        gate_id="G-M7-P1",
        allowed_role="backend",
        target_commit="abc1234",
        task="open backend phase 1 gate",
    )
    service.gate_review(
        "M7",
        role="pm",
        phase="1",
        gate_id="G-M7-P1",
        result="PASS",
        report_commit=report_commit,
        report_path=report_path,
        task="record blocked preflight review",
    )
    service.render("M7")
    service.gate_close(
        "M7",
        phase="1",
        gate_id="G-M7-P1",
        result="PASS",
        report_commit=report_commit,
        report_path=report_path,
        task="close blocked preflight gate",
    )
    service.render("M7")

    assert any(issue.status == "open" for issue in store.list_records("m7"))

    service.close_milestone("M7")

    milestone_issues = [
        issue
        for issue in store.list_records("m7")
        if issue.metadata.get("milestone") == "m7"
    ]
    assert milestone_issues
    assert all(issue.status == "closed" for issue in milestone_issues)
