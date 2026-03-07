from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts.devcoord import coord as coord_module
from scripts.devcoord.coord import (
    CoordError,
    CoordPaths,
    CoordService,
    MemoryCoordStore,
    SQLiteCoordStore,
    _resolve_paths,
    run_cli,
)
from scripts.devcoord.sqlite_store import SQLITE_SCHEMA_VERSION
from scripts.devcoord.store import BeadsCoordStore, CoordRecord


class FakeClock:
    def __init__(self, *timestamps: str) -> None:
        self._timestamps = list(timestamps)

    def __call__(self) -> str:
        if not self._timestamps:
            raise AssertionError("fake clock exhausted")
        return self._timestamps.pop(0)


def make_paths(tmp_path: Path) -> CoordPaths:
    workspace_root = tmp_path / "workspace"
    (workspace_root / "dev_docs" / "logs").mkdir(parents=True, exist_ok=True)
    (workspace_root / "dev_docs" / "progress").mkdir(parents=True, exist_ok=True)
    (workspace_root / ".git").mkdir(parents=True, exist_ok=True)
    return CoordPaths(
        workspace_root=workspace_root,
        beads_dir=workspace_root / ".coord" / "beads",
        git_common_dir=workspace_root / ".git",
    )


def init_git_repo_with_review(paths: CoordPaths, report_relpath: str) -> str:
    git_dir = paths.workspace_root / ".git"
    if git_dir.exists() and not (git_dir / "HEAD").exists():
        shutil.rmtree(git_dir)
    subprocess.run(
        ["git", "init"],
        cwd=paths.workspace_root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "NeoMAGI Tests"],
        cwd=paths.workspace_root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "tests@neomagi.local"],
        cwd=paths.workspace_root,
        check=True,
        capture_output=True,
        text=True,
    )
    report_path = paths.workspace_root / report_relpath
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("# review\n", "utf-8")
    subprocess.run(
        ["git", "add", report_relpath],
        cwd=paths.workspace_root,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "docs(tools): add review evidence"],
        cwd=paths.workspace_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=paths.workspace_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_init_creates_milestone_and_agent_beads(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)

    exit_code = run_cli(
        [
            "init",
            "--milestone",
            "M7",
            "--run-date",
            "2026-03-01",
        ],
        store=store,
        paths=paths,
    )

    assert exit_code == 0
    issues = store.list_records("m7")
    milestone_issues = [
        issue for issue in issues if issue.metadata.get("coord_kind") == "milestone"
    ]
    agent_issues = [issue for issue in issues if issue.metadata.get("coord_kind") == "agent"]

    assert len(milestone_issues) == 1
    assert milestone_issues[0].metadata["milestone"] == "m7"
    assert milestone_issues[0].metadata["run_date"] == "2026-03-01"
    assert {issue.metadata["role"] for issue in agent_issues} == {"pm", "backend", "tester"}


def test_apply_payload_file_executes_open_gate(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    init_payload_path = tmp_path / "init.json"
    init_payload_path.write_text(
        json.dumps(
            {
                "milestone": "M7",
                "run_date": "2026-03-01",
                "roles": ["pm", "backend", "tester"],
            }
        ),
        "utf-8",
    )

    run_cli(
        [
            "apply",
            "init",
            "--payload-file",
            str(init_payload_path),
        ],
        store=store,
        paths=paths,
    )

    payload_path = tmp_path / "open_gate.json"
    payload_path.write_text(
        json.dumps(
            {
                "milestone": "M7",
                "phase": "1",
                "gate_id": "G-M7-P1",
                "allowed_role": "backend",
                "target_commit": "abc1234",
                "task": "open backend phase 1 gate",
            }
        ),
        "utf-8",
    )

    exit_code = run_cli(
        [
            "apply",
            "open-gate",
            "--payload-file",
            str(payload_path),
        ],
        store=store,
        paths=paths,
        now_fn=FakeClock("2026-03-01T10:01:00Z"),
    )

    assert exit_code == 0
    issues = store.list_records("m7")
    assert any(issue.metadata.get("coord_kind") == "gate" for issue in issues)
    assert any(issue.metadata.get("event") == "GATE_OPEN_SENT" for issue in issues)


def test_apply_payload_stdin_executes_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    monkeypatch.setattr(
        coord_module.sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "milestone": "M7",
                    "run_date": "2026-03-01",
                    "roles": ["pm", "backend", "tester"],
                }
            )
        ),
    )

    exit_code = run_cli(
        [
            "apply",
            "init",
            "--payload-stdin",
        ],
        store=store,
        paths=paths,
    )

    assert exit_code == 0
    issues = store.list_records("m7")
    assert any(issue.metadata.get("coord_kind") == "milestone" for issue in issues)


def test_open_gate_canonicalizes_target_commit_when_git_can_resolve_it(tmp_path: Path) -> None:
    store = MemoryCoordStore()
    paths = make_paths(tmp_path)
    short_commit = init_git_repo_with_review(paths, "dev_docs/reviews/m7_phase1_2026-03-01.md")
    full_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=paths.workspace_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    service = CoordService(
        paths=paths,
        store=store,
        now_fn=FakeClock("2026-03-01T10:00:00Z"),
    )

    service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
    service.open_gate(
        "M7",
        phase="1",
        gate_id="G-M7-P1",
        allowed_role="backend",
        target_commit=short_commit,
        task="open gate with short git ref",
    )
    service.render("M7")

    audit = service.audit("M7")
    assert audit["open_gates"] == [
        {
            "gate": "G-M7-P1",
            "phase": "1",
            "status": "pending",
            "allowed_role": "backend",
            "target_commit": full_commit,
        }
    ]
    assert audit["pending_ack_messages"] == [
        {
            "command": "GATE_OPEN",
            "role": "backend",
            "gate": "G-M7-P1",
            "phase": "1",
            "target_commit": full_commit,
        }
    ]


def test_resolve_paths_defaults_to_workspace_root_for_root_beads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspace"
    (workspace_root / ".beads").mkdir(parents=True, exist_ok=True)
    (workspace_root / ".beads" / "metadata.json").write_text("{}", "utf-8")
    git_common_dir = workspace_root / ".git"
    git_common_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(coord_module, "_shared_workspace_root", lambda cwd: workspace_root)
    monkeypatch.setattr(coord_module, "_resolve_git_common_dir", lambda cwd: git_common_dir)

    paths = _resolve_paths(None)

    assert paths.workspace_root == workspace_root
    assert paths.beads_dir == workspace_root
    assert paths.lock_file == workspace_root / ".devcoord" / "coord.lock"
    assert paths.control_root == workspace_root / ".devcoord"


def test_resolve_paths_rejects_legacy_coord_beads_without_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspace"
    legacy_root = workspace_root / ".coord" / "beads" / ".beads"
    legacy_root.mkdir(parents=True, exist_ok=True)
    (legacy_root / "metadata.json").write_text("{}", "utf-8")
    git_common_dir = workspace_root / ".git"
    git_common_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(coord_module, "_shared_workspace_root", lambda cwd: workspace_root)
    monkeypatch.setattr(coord_module, "_resolve_git_common_dir", lambda cwd: git_common_dir)

    with pytest.raises(CoordError, match="legacy shared control plane detected at .coord/beads"):
        _resolve_paths(None)


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


def test_log_pending_and_audit_snapshot(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
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
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
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


def test_milestone_close_closes_all_milestone_beads(tmp_path: Path) -> None:
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


# ---------------------------------------------------------------------------
# F3: Store-focused contract tests
# ---------------------------------------------------------------------------


class TestMemoryCoordStoreContract:
    """Direct contract tests for MemoryCoordStore."""

    def test_create_returns_record_with_correct_fields(self) -> None:
        store = MemoryCoordStore()
        rec = store.create_record(
            title="test event",
            record_type="task",
            description="desc",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "event"},
        )
        assert rec.record_id
        assert rec.title == "test event"
        assert rec.record_type == "task"
        assert rec.status == "open"
        assert rec.has_label("coord")
        assert rec.metadata_str("coord_kind") == "event"

    def test_create_with_non_open_status(self) -> None:
        store = MemoryCoordStore()
        rec = store.create_record(
            title="closed item",
            record_type="task",
            description="",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "milestone"},
            status="closed",
        )
        assert rec.status == "closed"

    def test_list_records_filters_by_milestone(self) -> None:
        store = MemoryCoordStore()
        store.create_record(
            title="m1 event",
            record_type="task",
            description="",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "event"},
        )
        store.create_record(
            title="m2 event",
            record_type="task",
            description="",
            labels=["coord"],
            metadata={"milestone": "m2", "coord_kind": "event"},
        )
        assert len(store.list_records("m1")) == 1
        assert len(store.list_records("m2")) == 1
        assert store.list_records("m1")[0].title == "m1 event"

    def test_list_records_filters_by_kind(self) -> None:
        store = MemoryCoordStore()
        store.create_record(
            title="milestone rec",
            record_type="task",
            description="",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "milestone"},
        )
        store.create_record(
            title="event rec",
            record_type="task",
            description="",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "event"},
        )
        store.create_record(
            title="gate rec",
            record_type="task",
            description="",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "gate"},
        )

        assert len(store.list_records("m1")) == 3
        assert len(store.list_records("m1", kind="milestone")) == 1
        assert len(store.list_records("m1", kind="event")) == 1
        assert len(store.list_records("m1", kind="gate")) == 1
        assert len(store.list_records("m1", kind="agent")) == 0

    def test_list_records_excludes_non_coord_labels(self) -> None:
        store = MemoryCoordStore()
        store.create_record(
            title="no coord label",
            record_type="task",
            description="",
            labels=["other"],
            metadata={"milestone": "m1", "coord_kind": "event"},
        )
        assert store.list_records("m1") == []

    def test_update_record_merges_fields(self) -> None:
        store = MemoryCoordStore()
        rec = store.create_record(
            title="original",
            record_type="task",
            description="desc",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "event"},
        )
        updated = store.update_record(rec.record_id, title="changed", status="closed")
        assert updated.title == "changed"
        assert updated.status == "closed"
        assert updated.description == "desc"
        assert updated.metadata_str("coord_kind") == "event"

    def test_update_record_replaces_metadata(self) -> None:
        store = MemoryCoordStore()
        rec = store.create_record(
            title="t",
            record_type="task",
            description="",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "gate", "phase": "1"},
        )
        new_meta = {"milestone": "m1", "coord_kind": "gate", "phase": "1", "status": "closed"}
        updated = store.update_record(rec.record_id, metadata=new_meta)
        assert updated.metadata == new_meta

    def test_create_record_returns_same_as_list(self) -> None:
        store = MemoryCoordStore()
        created = store.create_record(
            title="roundtrip",
            record_type="task",
            description="d",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "event"},
        )
        listed = store.list_records("m1", kind="event")
        assert len(listed) == 1
        assert listed[0].record_id == created.record_id
        assert listed[0].title == created.title


class TestBeadsCoordStoreContract:
    """Contract tests for BeadsCoordStore with mocked subprocess."""

    def _make_store(self, tmp_path: Path) -> BeadsCoordStore:
        beads_dir = tmp_path / "beads"
        beads_dir.mkdir()
        (beads_dir / ".beads").mkdir()
        (beads_dir / ".beads" / "metadata.json").write_text("{}", "utf-8")
        return BeadsCoordStore(beads_dir, bd_bin="bd", dolt_bin="dolt")

    def _bd_list_payload(self, records: list[dict]) -> str:
        return json.dumps(records)

    def test_list_records_filters_by_kind(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = self._make_store(tmp_path)
        bd_output = self._bd_list_payload(
            [
                {
                    "id": "r1",
                    "title": "milestone",
                    "description": "",
                    "type": "task",
                    "status": "open",
                    "labels": "coord",
                    "metadata": json.dumps(
                        {"milestone": "m1", "coord_kind": "milestone"}
                    ),
                },
                {
                    "id": "r2",
                    "title": "event",
                    "description": "",
                    "type": "task",
                    "status": "open",
                    "labels": "coord",
                    "metadata": json.dumps(
                        {"milestone": "m1", "coord_kind": "event"}
                    ),
                },
                {
                    "id": "r3",
                    "title": "other milestone",
                    "description": "",
                    "type": "task",
                    "status": "open",
                    "labels": "coord",
                    "metadata": json.dumps(
                        {"milestone": "m2", "coord_kind": "event"}
                    ),
                },
            ]
        )

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout=bd_output, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        all_m1 = store.list_records("m1")
        assert len(all_m1) == 2

        milestones = store.list_records("m1", kind="milestone")
        assert len(milestones) == 1
        assert milestones[0].record_id == "r1"

        events = store.list_records("m1", kind="event")
        assert len(events) == 1
        assert events[0].record_id == "r2"

        gates = store.list_records("m1", kind="gate")
        assert gates == []

    def test_list_records_returns_empty_when_no_metadata_file(self, tmp_path: Path) -> None:
        beads_dir = tmp_path / "beads"
        beads_dir.mkdir()
        store = BeadsCoordStore(beads_dir)
        assert store.list_records("m1") == []

    def test_create_record_reloads_from_bd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = self._make_store(tmp_path)
        calls: list[list[str]] = []

        def fake_run(command, **kwargs):
            calls.append(list(command))
            cmd_str = " ".join(command)
            if "create" in cmd_str:
                return subprocess.CompletedProcess(command, 0, stdout="new-id-1\n", stderr="")
            if "list" in cmd_str:
                payload = json.dumps(
                    [
                        {
                            "id": "new-id-1",
                            "title": "created",
                            "description": "d",
                            "type": "task",
                            "status": "open",
                            "labels": "coord",
                            "metadata": json.dumps({"milestone": "m1", "coord_kind": "event"}),
                            "created_at": "2026-03-01T10:00:00Z",
                            "updated_at": "2026-03-01T10:00:00Z",
                        }
                    ]
                )
                return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        rec = store.create_record(
            title="created",
            record_type="task",
            description="d",
            labels=["coord"],
            metadata={"milestone": "m1", "coord_kind": "event"},
        )
        assert rec.record_id == "new-id-1"
        assert rec.created_at == "2026-03-01T10:00:00Z"
        create_calls = [c for c in calls if "create" in " ".join(c)]
        list_calls = [c for c in calls if "list" in " ".join(c)]
        assert len(create_calls) == 1
        assert len(list_calls) == 1

    def test_update_record_reloads_from_bd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = self._make_store(tmp_path)

        def fake_run(command, **kwargs):
            cmd_str = " ".join(command)
            if "update" in cmd_str:
                return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
            if "list" in cmd_str:
                payload = json.dumps(
                    [
                        {
                            "id": "r1",
                            "title": "updated title",
                            "description": "d",
                            "type": "task",
                            "status": "closed",
                            "labels": "coord",
                            "metadata": json.dumps({"milestone": "m1", "coord_kind": "gate"}),
                            "updated_at": "2026-03-01T11:00:00Z",
                        }
                    ]
                )
                return subprocess.CompletedProcess(command, 0, stdout=payload, stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        rec = store.update_record("r1", title="updated title", status="closed")
        assert rec.record_id == "r1"
        assert rec.title == "updated title"
        assert rec.status == "closed"
        assert rec.updated_at == "2026-03-01T11:00:00Z"

    def test_create_record_raises_on_empty_id(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = self._make_store(tmp_path)

        def fake_run(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(CoordError, match="empty record id"):
            store.create_record(
                title="bad",
                record_type="task",
                description="",
                labels=["coord"],
                metadata={"milestone": "m1", "coord_kind": "event"},
            )

    def test_reload_raises_when_record_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = self._make_store(tmp_path)

        call_count = 0

        def fake_run(command, **kwargs):
            nonlocal call_count
            call_count += 1
            cmd_str = " ".join(command)
            if "create" in cmd_str:
                return subprocess.CompletedProcess(command, 0, stdout="ghost-id\n", stderr="")
            if "list" in cmd_str:
                return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(CoordError, match="ghost-id not found after write"):
            store.create_record(
                title="ghost",
                record_type="task",
                description="",
                labels=["coord"],
                metadata={"milestone": "m1", "coord_kind": "event"},
            )


class TestCoordRecordFromMapping:
    """Unit tests for CoordRecord.from_mapping edge cases."""

    def test_string_labels_split(self) -> None:
        rec = CoordRecord.from_mapping(
            {"id": "1", "labels": "coord, extra", "metadata": {}}
        )
        assert rec.has_label("coord")
        assert rec.has_label("extra")

    def test_string_metadata_parsed(self) -> None:
        rec = CoordRecord.from_mapping(
            {
                "id": "1",
                "labels": [],
                "metadata": json.dumps({"coord_kind": "event"}),
            }
        )
        assert rec.metadata_str("coord_kind") == "event"

    def test_invalid_json_metadata_falls_back(self) -> None:
        rec = CoordRecord.from_mapping(
            {"id": "1", "labels": [], "metadata": "not-json"}
        )
        assert rec.metadata == {}

    def test_metadata_int_and_bool(self) -> None:
        rec = CoordRecord.from_mapping(
            {
                "id": "1",
                "labels": [],
                "metadata": {"count": 5, "flag": True, "str_int": "3"},
            }
        )
        assert rec.metadata_int("count") == 5
        assert rec.metadata_bool("flag") is True
        assert rec.metadata_int("str_int") == 3
        assert rec.metadata_int("missing", 99) == 99
        assert rec.metadata_bool("missing") is False


# ---------------------------------------------------------------------------
# SQLite store tests
# ---------------------------------------------------------------------------


def make_sqlite_paths(tmp_path: Path) -> CoordPaths:
    workspace_root = tmp_path / "workspace"
    (workspace_root / "dev_docs" / "logs").mkdir(parents=True, exist_ok=True)
    (workspace_root / "dev_docs" / "progress").mkdir(parents=True, exist_ok=True)
    (workspace_root / ".git").mkdir(parents=True, exist_ok=True)
    control_root = workspace_root / ".devcoord"
    control_root.mkdir(parents=True, exist_ok=True)
    return CoordPaths(
        workspace_root=workspace_root,
        beads_dir=workspace_root,
        git_common_dir=workspace_root / ".git",
        control_root=control_root,
    )


class TestSQLiteSchemaBootstrap:
    def test_init_creates_db_and_tables(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        store.init_store()
        assert paths.control_db.exists()
        conn = store._connect()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"milestones", "phases", "gates", "roles", "messages", "events"} <= tables
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == SQLITE_SCHEMA_VERSION
        store.close()

    def test_init_idempotent(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        store.init_store()
        store.init_store()
        store.close()

    def test_schema_version_mismatch_fails_closed(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        store.init_store()
        conn = store._connect()
        conn.execute(f"PRAGMA user_version={SQLITE_SCHEMA_VERSION + 1}")
        conn.commit()
        store.close()
        store2 = SQLiteCoordStore(paths.control_db)
        with pytest.raises(CoordError, match="incompatible schema version"):
            store2.init_store()
        store2.close()

    def test_empty_store_returns_no_records(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        store.init_store()
        assert store.list_records("m7") == []
        store.close()


class TestSQLiteFullLifecycle:
    def test_init_open_ack_heartbeat_phase_complete(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
            "2026-03-01T10:04:00Z",
            "2026-03-01T10:05:00Z",
            "2026-03-01T10:06:00Z",
            "2026-03-01T10:07:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)

        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))

        records = store.list_records("m7")
        kinds = sorted(rec.metadata_str("coord_kind") for rec in records)
        assert kinds.count("milestone") == 1
        assert kinds.count("agent") == 3

        service.open_gate(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            allowed_role="backend",
            target_commit="abc1234",
            task="open gate",
        )

        gates = store.list_records("m7", kind="gate")
        assert len(gates) == 1
        assert gates[0].metadata_str("gate_state") == "pending"

        service.ack(
            "M7",
            role="backend",
            command="GATE_OPEN",
            gate_id="G-M7-P1",
            commit="abc1234",
            phase="1",
            task="ACK",
        )

        gates = store.list_records("m7", kind="gate")
        assert gates[0].metadata_str("gate_state") == "open"

        service.heartbeat(
            "M7",
            role="backend",
            phase="1",
            status="working",
            task="coding",
            eta_min=30,
            gate_id="G-M7-P1",
            target_commit="abc1234",
        )

        service.phase_complete(
            "M7",
            role="backend",
            phase="1",
            gate_id="G-M7-P1",
            commit="abc1234",
            task="phase 1 done",
        )

        events = store.list_records("m7", kind="event")
        event_types = [e.metadata_str("event") for e in events]
        assert "GATE_OPEN_SENT" in event_types
        assert "ACK" in event_types
        assert "GATE_EFFECTIVE" in event_types
        assert "HEARTBEAT" in event_types
        assert "PHASE_COMPLETE" in event_types

        store.close()

    def test_render_and_audit_with_sqlite(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)
        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
        service.open_gate(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            allowed_role="backend",
            target_commit="abc1234",
            task="open gate",
        )

        service.render("M7")

        log_dir = paths.log_dir("m7", "2026-03-01")
        assert (log_dir / "heartbeat_events.jsonl").exists()
        assert (log_dir / "gate_state.md").exists()
        assert (log_dir / "watchdog_status.md").exists()
        assert paths.progress_file.exists()

        heartbeat_lines = [
            line
            for line in (log_dir / "heartbeat_events.jsonl").read_text("utf-8").splitlines()
            if line.strip()
        ]
        assert len(heartbeat_lines) > 0

        audit = service.audit("M7")
        assert audit["reconciled"] is True
        assert audit["received_events"] == len(heartbeat_lines)

        store.close()

    def test_gate_close_and_milestone_close_with_sqlite(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
            "2026-03-01T10:04:00Z",
            "2026-03-01T10:05:00Z",
            "2026-03-01T10:06:00Z",
            "2026-03-01T10:07:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)
        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
        service.open_gate(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            allowed_role="backend",
            target_commit="abc1234",
            task="open gate",
        )
        service.ack(
            "M7",
            role="backend",
            command="GATE_OPEN",
            gate_id="G-M7-P1",
            commit="abc1234",
            phase="1",
            task="ACK",
        )
        service.phase_complete(
            "M7",
            role="backend",
            phase="1",
            gate_id="G-M7-P1",
            commit="abc1234",
            task="done",
        )

        report_relpath = "dev_docs/reports/m7_p1_review.md"
        report_commit = init_git_repo_with_review(paths, report_relpath)

        service.gate_review(
            "M7",
            role="tester",
            phase="1",
            gate_id="G-M7-P1",
            result="PASS",
            report_commit=report_commit,
            report_path=report_relpath,
            task="review",
        )
        service.render("M7")
        service.gate_close(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            result="PASS",
            report_commit=report_commit,
            report_path=report_relpath,
            task="close gate",
        )
        service.render("M7")

        audit = service.audit("M7")
        assert audit["reconciled"] is True
        assert audit["open_gates"] == []

        service.close_milestone("M7")

        milestones = store.list_records("m7", kind="milestone")
        assert milestones[0].status == "closed"

        store.close()

    def test_ping_and_ack_with_sqlite(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
            "2026-03-01T10:04:00Z",
            "2026-03-01T10:05:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)
        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
        service.open_gate(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            allowed_role="backend",
            target_commit="abc1234",
            task="open gate",
        )
        service.ack(
            "M7",
            role="backend",
            command="GATE_OPEN",
            gate_id="G-M7-P1",
            commit="abc1234",
            phase="1",
            task="ACK",
        )
        service.ping(
            "M7",
            role="backend",
            phase="1",
            gate_id="G-M7-P1",
            task="checking in",
        )
        service.ack(
            "M7",
            role="backend",
            command="PING",
            gate_id="G-M7-P1",
            commit="abc1234",
            phase="1",
            task="ACK PING",
        )

        messages = store.list_records("m7", kind="message")
        ping_msgs = [m for m in messages if m.metadata_str("command") == "PING"]
        assert len(ping_msgs) == 1
        assert ping_msgs[0].metadata_bool("effective") is True

        store.close()

    def test_recovery_check_and_state_sync_ok(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
            "2026-03-01T10:04:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)
        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
        service.open_gate(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            allowed_role="backend",
            target_commit="abc1234",
            task="open gate",
        )
        service.ack(
            "M7",
            role="backend",
            command="GATE_OPEN",
            gate_id="G-M7-P1",
            commit="abc1234",
            phase="1",
            task="ACK",
        )
        service.recovery_check(
            "M7",
            role="backend",
            last_seen_gate="G-M7-P1",
            task="backend recovery",
        )
        service.state_sync_ok(
            "M7",
            role="backend",
            gate_id="G-M7-P1",
            target_commit="abc1234",
            task="sync ok",
        )

        events = store.list_records("m7", kind="event")
        event_types = [e.metadata_str("event") for e in events]
        assert "RECOVERY_CHECK" in event_types
        assert "STATE_SYNC_OK" in event_types

        store.close()


class TestSQLitePathResolution:
    def test_control_db_path_from_control_root(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        assert paths.control_db == paths.control_root / "control.db"

    def test_lock_file_in_control_root(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        assert paths.lock_file == paths.control_root / "coord.lock"

    def test_default_control_root_when_none(self, tmp_path: Path) -> None:
        paths = CoordPaths(
            workspace_root=tmp_path,
            beads_dir=tmp_path,
            git_common_dir=tmp_path / ".git",
        )
        assert paths.control_db == tmp_path / ".devcoord" / "control.db"
        assert paths.lock_file == tmp_path / ".devcoord" / "coord.lock"


class TestSQLiteBackendSelection:
    def test_auto_selects_sqlite_when_control_db_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace_root = tmp_path / "workspace"
        control_root = workspace_root / ".devcoord"
        control_root.mkdir(parents=True, exist_ok=True)
        (control_root / "control.db").write_text("", "utf-8")
        (workspace_root / ".git").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(coord_module, "_shared_workspace_root", lambda cwd: workspace_root)
        monkeypatch.setattr(
            coord_module, "_resolve_git_common_dir", lambda cwd: workspace_root / ".git"
        )
        paths = _resolve_paths(None)
        assert paths.control_root == control_root

    def test_auto_skips_legacy_beads_check_when_control_db_exists(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace_root = tmp_path / "workspace"
        control_root = workspace_root / ".devcoord"
        control_root.mkdir(parents=True, exist_ok=True)
        (control_root / "control.db").write_text("", "utf-8")
        legacy_root = workspace_root / ".coord" / "beads" / ".beads"
        legacy_root.mkdir(parents=True, exist_ok=True)
        (legacy_root / "metadata.json").write_text("{}", "utf-8")
        (workspace_root / ".git").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(coord_module, "_shared_workspace_root", lambda cwd: workspace_root)
        monkeypatch.setattr(
            coord_module, "_resolve_git_common_dir", lambda cwd: workspace_root / ".git"
        )
        paths = _resolve_paths(None)
        assert paths.control_root == control_root


class TestSQLiteStaleDetectedAndLogPending:
    def test_stale_detected_event(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)
        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
        service.open_gate(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            allowed_role="backend",
            target_commit="abc1234",
            task="open gate",
        )
        service.stale_detected(
            "M7",
            role="backend",
            phase="1",
            task="timeout check",
            gate_id="G-M7-P1",
            ping_count=3,
        )
        events = store.list_records("m7", kind="event")
        stale_events = [e for e in events if e.metadata_str("event") == "STALE_DETECTED"]
        assert len(stale_events) == 1
        assert stale_events[0].metadata.get("ping_count") == 3
        store.close()

    def test_log_pending_event(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock("2026-03-01T10:00:00Z", "2026-03-01T10:01:00Z")
        service = CoordService(paths=paths, store=store, now_fn=clock)
        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
        service.log_pending(
            "M7",
            phase="1",
            task="deferred log",
        )
        events = store.list_records("m7", kind="event")
        log_events = [e for e in events if e.metadata_str("event") == "LOG_PENDING"]
        assert len(log_events) == 1
        store.close()


class TestSQLiteUnconfirmedInstruction:
    def test_unconfirmed_instruction(self, tmp_path: Path) -> None:
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)
        service.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend", "tester"))
        service.open_gate(
            "M7",
            phase="1",
            gate_id="G-M7-P1",
            allowed_role="backend",
            target_commit="abc1234",
            task="open gate",
        )
        service.unconfirmed_instruction(
            "M7",
            role="backend",
            command="GATE_OPEN",
            phase="1",
            gate_id="G-M7-P1",
            task="unconfirmed",
            ping_count=5,
        )
        events = store.list_records("m7", kind="event")
        uc_events = [e for e in events if e.metadata_str("event") == "UNCONFIRMED_INSTRUCTION"]
        assert len(uc_events) == 1
        store.close()


class TestSQLiteFreshStartBootstrap:
    def test_fresh_start_from_empty_db(self, tmp_path: Path) -> None:
        """Full lifecycle from empty .devcoord/ - no beads needed."""
        paths = make_sqlite_paths(tmp_path)
        store = SQLiteCoordStore(paths.control_db)
        clock = FakeClock(
            "2026-03-01T10:00:00Z",
            "2026-03-01T10:01:00Z",
            "2026-03-01T10:02:00Z",
            "2026-03-01T10:03:00Z",
            "2026-03-01T10:04:00Z",
            "2026-03-01T10:05:00Z",
            "2026-03-01T10:06:00Z",
            "2026-03-01T10:07:00Z",
            "2026-03-01T10:08:00Z",
        )
        service = CoordService(paths=paths, store=store, now_fn=clock)

        service.init_control_plane(
            "P2-M1B", run_date="2026-03-01", roles=("pm", "backend", "tester")
        )
        service.open_gate(
            "P2-M1B",
            phase="1",
            gate_id="G0",
            allowed_role="backend",
            target_commit="deadbeef",
            task="open G0",
        )
        service.ack(
            "P2-M1B",
            role="backend",
            command="GATE_OPEN",
            gate_id="G0",
            commit="deadbeef",
            phase="1",
            task="ACK G0",
        )
        service.heartbeat(
            "P2-M1B",
            role="backend",
            phase="1",
            status="working",
            task="implementing",
            eta_min=60,
            gate_id="G0",
        )
        service.phase_complete(
            "P2-M1B",
            role="backend",
            phase="1",
            gate_id="G0",
            commit="deadbeef",
            task="P1 done",
        )

        report_relpath = "dev_docs/reports/p2m1b_review.md"
        report_commit = init_git_repo_with_review(paths, report_relpath)

        service.gate_review(
            "P2-M1B",
            role="tester",
            phase="1",
            gate_id="G0",
            result="PASS",
            report_commit=report_commit,
            report_path=report_relpath,
            task="review G0",
        )
        service.render("P2-M1B")
        service.gate_close(
            "P2-M1B",
            phase="1",
            gate_id="G0",
            result="PASS",
            report_commit=report_commit,
            report_path=report_relpath,
            task="close G0",
        )
        service.render("P2-M1B")

        audit = service.audit("P2-M1B")
        assert audit["reconciled"] is True
        assert audit["open_gates"] == []
        assert audit["pending_ack_messages"] == []

        service.close_milestone("P2-M1B")

        milestones = store.list_records("p2-m1b", kind="milestone")
        assert milestones[0].status == "closed"

        store.close()


class TestSQLiteWriteConflictSmoke:
    def test_busy_timeout_with_concurrent_write(self, tmp_path: Path) -> None:
        """Smoke test: second connection can write after first commits."""
        paths = make_sqlite_paths(tmp_path)
        store1 = SQLiteCoordStore(paths.control_db)
        store1.init_store()
        store2 = SQLiteCoordStore(paths.control_db)
        store2.init_store()

        clock1 = FakeClock("2026-03-01T10:00:00Z")
        service1 = CoordService(paths=paths, store=store1, now_fn=clock1)
        service1.init_control_plane("M7", run_date="2026-03-01", roles=("pm", "backend"))

        records = store2.list_records("m7")
        assert len(records) > 0

        store1.close()
        store2.close()
