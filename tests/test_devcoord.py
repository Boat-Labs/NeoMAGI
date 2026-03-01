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
    MemoryIssueStore,
    _resolve_paths,
    run_cli,
)


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
    store = MemoryIssueStore()
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
    issues = store.load_issues()
    milestone_issues = [
        issue for issue in issues if issue.metadata.get("coord_kind") == "milestone"
    ]
    agent_issues = [issue for issue in issues if issue.metadata.get("coord_kind") == "agent"]

    assert len(milestone_issues) == 1
    assert milestone_issues[0].metadata["milestone"] == "m7"
    assert milestone_issues[0].metadata["run_date"] == "2026-03-01"
    assert {issue.metadata["role"] for issue in agent_issues} == {"pm", "backend", "tester"}


def test_apply_payload_file_executes_open_gate(tmp_path: Path) -> None:
    store = MemoryIssueStore()
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
    issues = store.load_issues()
    assert any(issue.metadata.get("coord_kind") == "gate" for issue in issues)
    assert any(issue.metadata.get("event") == "GATE_OPEN_SENT" for issue in issues)


def test_apply_payload_stdin_executes_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryIssueStore()
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
    issues = store.load_issues()
    assert any(issue.metadata.get("coord_kind") == "milestone" for issue in issues)


def test_open_gate_canonicalizes_target_commit_when_git_can_resolve_it(tmp_path: Path) -> None:
    store = MemoryIssueStore()
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
    assert paths.lock_file == git_common_dir / "coord.lock"


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
    store = MemoryIssueStore()
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
    store = MemoryIssueStore()
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
    store = MemoryIssueStore()
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
    store = MemoryIssueStore()
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
    store = MemoryIssueStore()
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
    store = MemoryIssueStore()
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
    store = MemoryIssueStore()
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
    store = MemoryIssueStore()
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
    store = MemoryIssueStore()
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
    store = MemoryIssueStore()
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
    store = MemoryIssueStore()
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
    store = MemoryIssueStore()
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
    store = MemoryIssueStore()
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
    store = MemoryIssueStore()
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
