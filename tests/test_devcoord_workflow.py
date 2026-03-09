from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.devcoord import coord as coord_module
from tests.devcoord_helpers import (
    CoordError,
    CoordService,
    FakeClock,
    MemoryCoordStore,
    _resolve_paths,
    make_paths,
    run_cli,
)


def test_resolve_paths_returns_sqlite_control_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspace"
    git_common_dir = workspace_root / ".git"
    git_common_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(coord_module, "_shared_workspace_root", lambda cwd: workspace_root)
    monkeypatch.setattr(coord_module, "_resolve_git_common_dir", lambda cwd: git_common_dir)

    paths = _resolve_paths()

    assert paths.workspace_root == workspace_root
    assert paths.control_root == workspace_root / ".devcoord"
    assert paths.control_db == workspace_root / ".devcoord" / "control.db"
    assert paths.lock_file == workspace_root / ".devcoord" / "coord.lock"


def test_ack_fails_closed_without_pending_message(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    service = CoordService(
        paths=paths,
        store=store,
        now_fn=FakeClock("2026-03-01T10:00:00Z", "2026-03-01T10:05:00Z"),
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
        phase="1",
        task="ACK GATE_OPEN",
    )

    with pytest.raises(CoordError, match="no pending GATE_OPEN message"):
        service.ack(
            "M7",
            role="backend",
            command="GATE_OPEN",
            gate_id="G-M7-P1",
            commit="abc1234",
            phase="1",
            task="ACK GATE_OPEN",
        )


def test_ack_deduplicates_duplicate_pending_gate_open(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    clock = FakeClock(
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:06:00Z",
        "2026-03-01T10:10:00Z",
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
    service.open_gate(
        "M7",
        phase="1",
        gate_id="G-M7-P1",
        allowed_role="backend",
        target_commit="abc1234",
        task="re-open same backend gate by mistake",
    )
    service.ack(
        "M7",
        role="backend",
        command="GATE_OPEN",
        gate_id="G-M7-P1",
        commit="abc1234",
        task="ACK duplicate GATE_OPEN for same gate",
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
        "GATE_OPEN_SENT",
    ]

    audit = service.audit("M7")
    assert audit["pending_ack_messages"] == []
    assert audit["open_gates"] == [
        {
            "gate": "G-M7-P1",
            "phase": "1",
            "status": "open",
            "allowed_role": "backend",
            "target_commit": "abc1234",
        }
    ]


def test_recovery_check_and_state_sync_render_projection(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    clock = FakeClock(
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:20:00Z",
        "2026-03-01T10:22:00Z",
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
    service.recovery_check(
        "M7",
        role="backend",
        last_seen_gate="G-M7-P1",
        task="context reset, requesting state sync",
    )
    service.state_sync_ok(
        "M7",
        role="backend",
        gate_id="G-M7-P1",
        target_commit="abc1234",
        task="state sync complete after recovery",
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
        "RECOVERY_CHECK",
        "STATE_SYNC_OK",
    ]
    assert heartbeat_events[3]["last_seen_gate"] == "G-M7-P1"
    assert heartbeat_events[3]["allowed_role"] == "backend"
    assert heartbeat_events[4]["sync_role"] == "backend"
    assert heartbeat_events[4]["allowed_role"] == "backend"

    watchdog_status = (log_dir / "watchdog_status.md").read_text("utf-8")
    assert (
        "| backend | idle | 2026-03-01T10:22:00Z | state sync complete after recovery | none | "
        "resume at G-M7-P1 (abc1234) |"
    ) in watchdog_status


def test_recovery_check_is_idempotent_for_same_gate(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    clock = FakeClock(
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:20:00Z",
        "2026-03-01T10:21:00Z",
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
    service.recovery_check(
        "M7",
        role="backend",
        last_seen_gate="G-M7-P1",
        task="context reset, requesting state sync",
    )
    service.recovery_check(
        "M7",
        role="backend",
        last_seen_gate="G-M7-P1",
        task="same recovery check re-sent after CLI retry",
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
        "RECOVERY_CHECK",
    ]


def test_stale_detected_marks_watchdog_risk(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    clock = FakeClock(
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:40:00Z",
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
    service.stale_detected(
        "M7",
        role="backend",
        phase="1",
        gate_id="G-M7-P1",
        target_commit="abc1234",
        ping_count=2,
        task="two unanswered PINGs; suspected stale",
    )
    service.render("M7")

    log_dir = paths.log_dir("m7", "2026-03-01")
    heartbeat_events = [
        json.loads(line)
        for line in (log_dir / "heartbeat_events.jsonl").read_text("utf-8").splitlines()
        if line.strip()
    ]
    assert heartbeat_events[-1]["event"] == "STALE_DETECTED"
    assert heartbeat_events[-1]["ping_count"] == 2

    watchdog_status = (log_dir / "watchdog_status.md").read_text("utf-8")
    assert (
        "| backend | stuck | 2026-03-01T10:40:00Z | two unanswered PINGs; suspected stale | "
        "suspected_stale | stale detected on G-M7-P1; investigate and recover |"
    ) in watchdog_status


def test_ping_and_unconfirmed_instruction_render_projection(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    clock = FakeClock(
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:20:00Z",
        "2026-03-01T10:31:00Z",
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
    service.ping(
        "M7",
        role="backend",
        phase="1",
        gate_id="G-M7-P1",
        target_commit="abc1234",
        task="PING backend after 20 minutes idle",
    )
    service.unconfirmed_instruction(
        "M7",
        role="backend",
        command="GATE_OPEN",
        phase="1",
        gate_id="G-M7-P1",
        target_commit="abc1234",
        ping_count=2,
        task="record unconfirmed gate open after repeated PING",
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
        "PING_SENT",
        "UNCONFIRMED_INSTRUCTION",
    ]
    assert heartbeat_events[3]["target_role"] == "backend"
    assert heartbeat_events[4]["command_name"] == "GATE_OPEN"
    assert heartbeat_events[4]["target_role"] == "backend"
    assert heartbeat_events[4]["ping_count"] == 2


def test_log_pending_and_audit_snapshot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    clock = FakeClock(
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:20:00Z",
        "2026-03-01T10:21:00Z",
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
    service.log_pending(
        "M7",
        phase="1",
        gate_id="G-M7-P1",
        target_commit="abc1234",
        task="append-first delayed; will backfill next PM turn",
    )
    service.ping(
        "M7",
        role="backend",
        phase="1",
        gate_id="G-M7-P1",
        target_commit="abc1234",
        task="PING backend after append-first recovery",
    )
    service.render("M7")

    audit = service.audit("M7")
    assert audit["reconciled"] is True
    assert audit["received_events"] == 5
    assert audit["logged_events"] == 5
    assert audit["open_gates"] == [
        {
            "gate": "G-M7-P1",
            "phase": "1",
            "status": "open",
            "allowed_role": "backend",
            "target_commit": "abc1234",
        }
    ]
    assert audit["pending_ack_messages"] == [
        {
            "command": "PING",
            "role": "backend",
            "gate": "G-M7-P1",
            "phase": "1",
            "target_commit": "abc1234",
        }
    ]
    assert len(audit["log_pending_events"]) == 1
    assert audit["log_pending_events"][0]["event"] == "LOG_PENDING"

    exit_code = run_cli(
        [
            "audit",
            "--milestone",
            "M7",
        ],
        store=store,
        paths=paths,
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["reconciled"] is True
    assert payload["pending_ack_messages"][0]["command"] == "PING"
    assert payload["log_pending_events"][0]["event"] == "LOG_PENDING"


def test_state_sync_ok_fails_closed_on_target_commit_mismatch(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    service = CoordService(
        paths=paths,
        store=store,
        now_fn=FakeClock("2026-03-01T10:01:00Z", "2026-03-01T10:05:00Z"),
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

    with pytest.raises(CoordError, match="state sync target_commit mismatch"):
        service.state_sync_ok(
            "M7",
            role="backend",
            gate_id="G-M7-P1",
            target_commit="wrong999",
            task="state sync complete after recovery",
        )
