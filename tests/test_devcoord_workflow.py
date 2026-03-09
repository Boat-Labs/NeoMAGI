from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.devcoord import coord as coord_module
from tests.devcoord_helpers import (
    DEFAULT_GATE_ID,
    DEFAULT_MILESTONE,
    DEFAULT_PHASE,
    DEFAULT_TARGET_COMMIT,
    CoordError,
    CoordService,
    _resolve_paths,
    ack_default_gate_open,
    event_names,
    init_default_control_plane,
    make_memory_service,
    open_default_gate,
    read_heartbeat_events,
    rendered_log_dir,
    run_cli,
)


def _init_open_ack(service: CoordService) -> None:
    init_default_control_plane(service)
    open_default_gate(service)
    ack_default_gate_open(service)


def _open_gate_snapshot(status: str = "open") -> dict[str, str]:
    return {
        "gate": DEFAULT_GATE_ID,
        "phase": DEFAULT_PHASE,
        "status": status,
        "allowed_role": "backend",
        "target_commit": DEFAULT_TARGET_COMMIT,
    }


def _pending_ack_snapshot(command: str) -> dict[str, str]:
    return {
        "command": command,
        "role": "backend",
        "gate": DEFAULT_GATE_ID,
        "phase": DEFAULT_PHASE,
        "target_commit": DEFAULT_TARGET_COMMIT,
    }


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
    _, _, service = make_memory_service(
        tmp_path, "2026-03-01T10:00:00Z", "2026-03-01T10:05:00Z"
    )

    init_default_control_plane(service)
    open_default_gate(service)
    ack_default_gate_open(service, phase=DEFAULT_PHASE, task="ACK GATE_OPEN")

    with pytest.raises(CoordError, match="no pending GATE_OPEN message"):
        ack_default_gate_open(service, phase=DEFAULT_PHASE, task="ACK GATE_OPEN")


def test_ack_deduplicates_duplicate_pending_gate_open(tmp_path: Path) -> None:
    paths, _, service = make_memory_service(
        tmp_path,
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:06:00Z",
        "2026-03-01T10:10:00Z",
    )

    _init_open_ack(service)
    open_default_gate(service, task="re-open same backend gate by mistake")
    ack_default_gate_open(service, task="ACK duplicate GATE_OPEN for same gate")
    service.render("M7")

    heartbeat_events = read_heartbeat_events(paths)
    assert event_names(heartbeat_events) == [
        "GATE_OPEN_SENT",
        "ACK",
        "GATE_EFFECTIVE",
        "GATE_OPEN_SENT",
    ]

    audit = service.audit("M7")
    assert audit["pending_ack_messages"] == []
    assert audit["open_gates"] == [
        {
            "gate": DEFAULT_GATE_ID,
            "phase": DEFAULT_PHASE,
            "status": "open",
            "allowed_role": "backend",
            "target_commit": DEFAULT_TARGET_COMMIT,
        }
    ]


def test_recovery_check_and_state_sync_render_projection(tmp_path: Path) -> None:
    paths, _, service = make_memory_service(
        tmp_path,
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:20:00Z",
        "2026-03-01T10:22:00Z",
    )

    _init_open_ack(service)
    service.recovery_check(
        DEFAULT_MILESTONE,
        role="backend",
        last_seen_gate=DEFAULT_GATE_ID,
        task="context reset, requesting state sync",
    )
    service.state_sync_ok(
        DEFAULT_MILESTONE,
        role="backend",
        gate_id=DEFAULT_GATE_ID,
        target_commit=DEFAULT_TARGET_COMMIT,
        task="state sync complete after recovery",
    )
    service.render(DEFAULT_MILESTONE)

    heartbeat_events = read_heartbeat_events(paths)
    assert event_names(heartbeat_events) == [
        "GATE_OPEN_SENT",
        "ACK",
        "GATE_EFFECTIVE",
        "RECOVERY_CHECK",
        "STATE_SYNC_OK",
    ]
    assert heartbeat_events[3]["last_seen_gate"] == DEFAULT_GATE_ID
    assert heartbeat_events[3]["allowed_role"] == "backend"
    assert heartbeat_events[4]["sync_role"] == "backend"
    assert heartbeat_events[4]["allowed_role"] == "backend"

    watchdog_status = (rendered_log_dir(paths) / "watchdog_status.md").read_text("utf-8")
    assert (
        "| backend | idle | 2026-03-01T10:22:00Z | state sync complete after recovery | none | "
        f"resume at {DEFAULT_GATE_ID} ({DEFAULT_TARGET_COMMIT}) |"
    ) in watchdog_status


def test_recovery_check_is_idempotent_for_same_gate(tmp_path: Path) -> None:
    paths, _, service = make_memory_service(
        tmp_path,
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:20:00Z",
        "2026-03-01T10:21:00Z",
    )

    _init_open_ack(service)
    service.recovery_check(
        DEFAULT_MILESTONE,
        role="backend",
        last_seen_gate=DEFAULT_GATE_ID,
        task="context reset, requesting state sync",
    )
    service.recovery_check(
        DEFAULT_MILESTONE,
        role="backend",
        last_seen_gate=DEFAULT_GATE_ID,
        task="same recovery check re-sent after CLI retry",
    )
    service.render(DEFAULT_MILESTONE)

    assert event_names(read_heartbeat_events(paths)) == [
        "GATE_OPEN_SENT",
        "ACK",
        "GATE_EFFECTIVE",
        "RECOVERY_CHECK",
    ]


def test_stale_detected_marks_watchdog_risk(tmp_path: Path) -> None:
    paths, _, service = make_memory_service(
        tmp_path,
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:40:00Z",
    )

    _init_open_ack(service)
    service.stale_detected(
        DEFAULT_MILESTONE,
        role="backend",
        phase=DEFAULT_PHASE,
        gate_id=DEFAULT_GATE_ID,
        target_commit=DEFAULT_TARGET_COMMIT,
        ping_count=2,
        task="two unanswered PINGs; suspected stale",
    )
    service.render(DEFAULT_MILESTONE)

    heartbeat_events = read_heartbeat_events(paths)
    assert heartbeat_events[-1]["event"] == "STALE_DETECTED"
    assert heartbeat_events[-1]["ping_count"] == 2

    watchdog_status = (rendered_log_dir(paths) / "watchdog_status.md").read_text("utf-8")
    assert (
        "| backend | stuck | 2026-03-01T10:40:00Z | two unanswered PINGs; suspected stale | "
        f"suspected_stale | stale detected on {DEFAULT_GATE_ID}; investigate and recover |"
    ) in watchdog_status


def test_ping_and_unconfirmed_instruction_render_projection(tmp_path: Path) -> None:
    paths, _, service = make_memory_service(
        tmp_path,
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:20:00Z",
        "2026-03-01T10:31:00Z",
    )

    _init_open_ack(service)
    service.ping(
        DEFAULT_MILESTONE,
        role="backend",
        phase=DEFAULT_PHASE,
        gate_id=DEFAULT_GATE_ID,
        target_commit=DEFAULT_TARGET_COMMIT,
        task="PING backend after 20 minutes idle",
    )
    service.unconfirmed_instruction(
        DEFAULT_MILESTONE,
        role="backend",
        command="GATE_OPEN",
        phase=DEFAULT_PHASE,
        gate_id=DEFAULT_GATE_ID,
        target_commit=DEFAULT_TARGET_COMMIT,
        ping_count=2,
        task="record unconfirmed gate open after repeated PING",
    )
    service.render(DEFAULT_MILESTONE)

    heartbeat_events = read_heartbeat_events(paths)
    assert event_names(heartbeat_events) == [
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
    paths, store, service = make_memory_service(
        tmp_path,
        "2026-03-01T10:01:00Z",
        "2026-03-01T10:05:00Z",
        "2026-03-01T10:20:00Z",
        "2026-03-01T10:21:00Z",
    )

    _init_open_ack(service)
    service.log_pending(
        DEFAULT_MILESTONE,
        phase=DEFAULT_PHASE,
        gate_id=DEFAULT_GATE_ID,
        target_commit=DEFAULT_TARGET_COMMIT,
        task="append-first delayed; will backfill next PM turn",
    )
    service.ping(
        DEFAULT_MILESTONE,
        role="backend",
        phase=DEFAULT_PHASE,
        gate_id=DEFAULT_GATE_ID,
        target_commit=DEFAULT_TARGET_COMMIT,
        task="PING backend after append-first recovery",
    )
    service.render(DEFAULT_MILESTONE)

    audit = service.audit(DEFAULT_MILESTONE)
    assert (audit["reconciled"], audit["received_events"], audit["logged_events"]) == (
        True,
        5,
        5,
    )
    assert audit["open_gates"] == [_open_gate_snapshot()]
    assert audit["pending_ack_messages"] == [_pending_ack_snapshot("PING")]
    assert [event["event"] for event in audit["log_pending_events"]] == ["LOG_PENDING"]

    exit_code = run_cli(["audit", "--milestone", DEFAULT_MILESTONE], store=store, paths=paths)
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["reconciled"] is True
    assert payload["pending_ack_messages"][0]["command"] == "PING"
    assert payload["log_pending_events"][0]["event"] == "LOG_PENDING"


def test_state_sync_ok_fails_closed_on_target_commit_mismatch(tmp_path: Path) -> None:
    _, _, service = make_memory_service(
        tmp_path, "2026-03-01T10:01:00Z", "2026-03-01T10:05:00Z"
    )

    init_default_control_plane(service)
    open_default_gate(service)

    with pytest.raises(CoordError, match="state sync target_commit mismatch"):
        service.state_sync_ok(
            DEFAULT_MILESTONE,
            role="backend",
            gate_id=DEFAULT_GATE_ID,
            target_commit="wrong999",
            task="state sync complete after recovery",
        )
