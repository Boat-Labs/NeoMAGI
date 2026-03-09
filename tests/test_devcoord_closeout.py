from __future__ import annotations

from pathlib import Path

import pytest

from tests.devcoord_helpers import (
    DEFAULT_GATE_ID,
    DEFAULT_MILESTONE,
    DEFAULT_PHASE,
    DEFAULT_TARGET_COMMIT,
    CoordError,
    CoordService,
    ack_default_gate_open,
    event_names,
    gate_close_default,
    gate_review_default,
    init_default_control_plane,
    init_git_repo_with_review,
    make_memory_service,
    open_default_gate,
    phase_complete_default,
    read_heartbeat_events,
    rendered_log_dir,
)


def _open_ack(service: CoordService) -> None:
    init_default_control_plane(service)
    open_default_gate(service)
    ack_default_gate_open(service)


def _review_closed_gate(
    service: CoordService,
    report_commit: str,
    report_path: str,
    *,
    reviewer: str = "tester",
    review_task: str = "Phase 1 review PASS",
    close_task: str = "close gate after review",
) -> None:
    gate_review_default(
        service,
        report_commit,
        report_path,
        role=reviewer,
        task=review_task,
    )
    service.render(DEFAULT_MILESTONE)
    gate_close_default(service, report_commit, report_path, task=close_task)
    service.render(DEFAULT_MILESTONE)


def _assert_generated_progress(progress: str, *, status: str, next_line: str) -> None:
    assert progress.count("<!-- devcoord:begin milestone=m7 -->") == 1
    assert "## 2026-03-01 (generated) | M7" in progress
    assert f"- Status: {status}" in progress
    assert next_line in progress


def _assert_full_flow_projection(paths, heartbeat_events: list[dict[str, object]]) -> None:
    log_dir = rendered_log_dir(paths)
    assert event_names(heartbeat_events) == [
        "GATE_OPEN_SENT",
        "ACK",
        "GATE_EFFECTIVE",
        "HEARTBEAT",
        "PHASE_COMPLETE",
    ]
    assert [event["event_seq"] for event in heartbeat_events] == [1, 2, 3, 4, 5]
    assert (
        heartbeat_events[1]["ack_of"],
        heartbeat_events[3]["branch"],
        heartbeat_events[4]["target_commit"],
    ) == ("GATE_OPEN", "feat/backend-m7-control-plane", "def5678")
    assert (
        f"| {DEFAULT_GATE_ID} | {DEFAULT_PHASE} | open |  | "
        "2026-03-01T10:05:00Z |  | def5678 |  |"
    ) in (log_dir / "gate_state.md").read_text("utf-8")
    watchdog_status = (log_dir / "watchdog_status.md").read_text("utf-8")
    assert "| tester | idle |  |  | none | awaiting gate |" in watchdog_status
    assert (
        "| backend | done | 2026-03-01T10:15:00Z | Phase 1 complete | none | "
        f"waiting for next gate after {DEFAULT_GATE_ID} |"
    ) in watchdog_status


def test_full_flow_renders_projection_files(tmp_path: Path) -> None:
    paths, _, service = make_memory_service(
        tmp_path,
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:10:00Z",
        "2026-03-01T10:15:00Z",
    )

    _open_ack(service)
    service.heartbeat(
        DEFAULT_MILESTONE,
        role="backend",
        phase=DEFAULT_PHASE,
        status="working",
        task="running implementation",
        eta_min=25,
        gate_id=DEFAULT_GATE_ID,
        target_commit=DEFAULT_TARGET_COMMIT,
        branch="feat/backend-m7-control-plane",
    )
    phase_complete_default(service, commit="def5678", branch="feat/backend-m7-control-plane")
    service.render(DEFAULT_MILESTONE)

    heartbeat_events = read_heartbeat_events(paths)
    _assert_full_flow_projection(paths, heartbeat_events)
    _assert_generated_progress(
        paths.progress_file.read_text("utf-8"),
        status="in_progress",
        next_line=f"- Next: 继续推进 {DEFAULT_GATE_ID}，当前 allowed_role=backend",
    )


def test_phase_complete_is_idempotent_for_same_gate_commit(tmp_path: Path) -> None:
    paths, _, service = make_memory_service(
        tmp_path,
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:10:00Z",
        "2026-03-01T10:15:00Z",
    )

    _open_ack(service)
    phase_complete_default(service, commit="def5678", branch="feat/backend-m7-control-plane")
    phase_complete_default(
        service,
        commit="def5678",
        task="Phase 1 complete duplicate retry",
        branch="feat/backend-m7-control-plane",
    )
    service.render(DEFAULT_MILESTONE)

    heartbeat_events = read_heartbeat_events(paths)
    assert event_names(heartbeat_events) == [
        "GATE_OPEN_SENT",
        "ACK",
        "GATE_EFFECTIVE",
        "PHASE_COMPLETE",
    ]
    assert heartbeat_events[-1]["target_commit"] == "def5678"


def test_gate_review_and_close_render_closed_state(tmp_path: Path) -> None:
    paths, _, service = make_memory_service(
        tmp_path,
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:10:00Z",
        "2026-03-01T10:15:00Z",
        "2026-03-01T10:20:00Z",
        "2026-03-01T10:25:00Z",
    )
    report_path = "dev_docs/reviews/m7_phase1_2026-03-01.md"
    report_commit = init_git_repo_with_review(paths, report_path)

    _open_ack(service)
    phase_complete_default(service, commit="def5678", branch="main")
    _review_closed_gate(service, report_commit, report_path)

    log_dir = rendered_log_dir(paths)
    heartbeat_events = read_heartbeat_events(paths)
    assert event_names(heartbeat_events) == [
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
        f"| {DEFAULT_GATE_ID} | {DEFAULT_PHASE} | closed | PASS | 2026-03-01T10:05:00Z | "
        "2026-03-01T10:20:00Z | def5678 | "
        f"{report_path} ({report_commit}) |"
    ) in gate_state

    watchdog_status = (log_dir / "watchdog_status.md").read_text("utf-8")
    assert (
        "| tester | done | 2026-03-01T10:15:00Z | Phase 1 review PASS | none | "
        "review submitted for G-M7-P1 |"
    ) in watchdog_status

    progress = paths.progress_file.read_text("utf-8")
    _assert_generated_progress(
        progress,
        status="done",
        next_line=f"`{report_path}` ({report_commit})",
    )


def test_gate_close_requires_rendered_reconciliation(tmp_path: Path) -> None:
    paths, _, service = make_memory_service(
        tmp_path,
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:10:00Z",
        "2026-03-01T10:15:00Z",
    )
    report_path = "dev_docs/reviews/m7_phase1_2026-03-01.md"
    report_commit = init_git_repo_with_review(paths, report_path)

    _open_ack(service)
    gate_review_default(service, report_commit, report_path)

    with pytest.raises(
        CoordError,
        match="cannot close gate before heartbeat_events.jsonl has been rendered",
    ):
        gate_close_default(service, report_commit, report_path)


def test_gate_close_requires_visible_report_commit(tmp_path: Path) -> None:
    paths, _, service = make_memory_service(
        tmp_path,
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:10:00Z",
        "2026-03-01T10:15:00Z",
    )
    report_path = "dev_docs/reviews/m7_phase1_2026-03-01.md"
    init_git_repo_with_review(paths, report_path)

    _open_ack(service)
    gate_review_default(
        service,
        "deadbeef",
        report_path,
    )
    service.render(DEFAULT_MILESTONE)

    with pytest.raises(CoordError, match="git cat-file -e deadbeef"):
        gate_close_default(service, "deadbeef", report_path)


def test_audit_ignores_pending_ack_for_closed_gate(tmp_path: Path) -> None:
    paths, store, service = make_memory_service(
        tmp_path,
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:10:00Z",
        "2026-03-01T10:15:00Z",
    )
    report_path = "dev_docs/reviews/m7_phase1_2026-03-01.md"
    report_commit = init_git_repo_with_review(paths, report_path)

    init_default_control_plane(service)
    open_default_gate(service, task="open backend gate that stays unacked")
    _review_closed_gate(
        service,
        report_commit,
        report_path,
        reviewer="pm",
        review_task="record blocked preflight review",
        close_task="close blocked preflight gate",
    )

    audit = service.audit(DEFAULT_MILESTONE)
    assert audit["open_gates"] == []
    assert audit["pending_ack_messages"] == []


def test_milestone_close_requires_clean_audit(tmp_path: Path) -> None:
    _, _, service = make_memory_service(tmp_path, "2026-03-01T10:01:00Z")

    init_default_control_plane(service)
    open_default_gate(service)
    service.render(DEFAULT_MILESTONE)

    with pytest.raises(CoordError, match="cannot close milestone while gates remain open"):
        service.close_milestone(DEFAULT_MILESTONE)


def test_milestone_close_closes_all_milestone_records(tmp_path: Path) -> None:
    paths, store, service = make_memory_service(
        tmp_path,
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:10:00Z",
        "2026-03-01T10:15:00Z",
    )
    report_path = "dev_docs/reviews/m7_phase1_2026-03-01.md"
    report_commit = init_git_repo_with_review(paths, report_path)

    init_default_control_plane(service)
    open_default_gate(service)
    _review_closed_gate(
        service,
        report_commit,
        report_path,
        reviewer="pm",
        review_task="record blocked preflight review",
        close_task="close blocked preflight gate",
    )

    assert any(issue.status == "open" for issue in store.list_records("m7"))

    service.close_milestone(DEFAULT_MILESTONE)

    milestone_issues = [
        issue
        for issue in store.list_records("m7")
        if issue.metadata.get("milestone") == "m7"
    ]
    assert milestone_issues
    assert all(issue.status == "closed" for issue in milestone_issues)
