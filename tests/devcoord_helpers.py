from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from scripts.devcoord.coord import (
    CoordError,
    CoordPaths,
    CoordService,
    MemoryCoordStore,
    SQLiteCoordStore,
    _normalize_argv,
    _resolve_paths,
    build_parser,
    run_cli,
)
from scripts.devcoord.sqlite_store import SQLITE_SCHEMA_VERSION
from scripts.devcoord.store import CoordRecord

__all__ = [
    "CoordError",
    "CoordPaths",
    "CoordRecord",
    "CoordService",
    "DEFAULT_ALLOWED_ROLE",
    "DEFAULT_GATE_ID",
    "DEFAULT_MILESTONE",
    "DEFAULT_PHASE",
    "DEFAULT_ROLES",
    "DEFAULT_RUN_DATE",
    "DEFAULT_TARGET_COMMIT",
    "FakeClock",
    "MemoryCoordStore",
    "SQLITE_SCHEMA_VERSION",
    "SQLiteCoordStore",
    "_normalize_argv",
    "_resolve_paths",
    "ack_default_gate_open",
    "build_parser",
    "event_names",
    "gate_close_default",
    "gate_review_default",
    "init_default_control_plane",
    "init_git_repo_with_review",
    "make_memory_service",
    "make_paths",
    "make_sqlite_service",
    "make_sqlite_paths",
    "open_default_gate",
    "phase_complete_default",
    "read_heartbeat_events",
    "rendered_log_dir",
    "run_cli",
    "write_json",
]

DEFAULT_MILESTONE = "M7"
DEFAULT_RUN_DATE = "2026-03-01"
DEFAULT_PHASE = "1"
DEFAULT_GATE_ID = "G-M7-P1"
DEFAULT_ALLOWED_ROLE = "backend"
DEFAULT_TARGET_COMMIT = "abc1234"
DEFAULT_ROLES = ("pm", "backend", "tester")


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
    control_root = workspace_root / ".devcoord"
    control_root.mkdir(parents=True, exist_ok=True)
    return CoordPaths(
        workspace_root=workspace_root,
        git_common_dir=workspace_root / ".git",
        control_root=control_root,
    )


def make_sqlite_paths(tmp_path: Path) -> CoordPaths:
    return make_paths(tmp_path)


def make_memory_service(
    tmp_path: Path, *timestamps: str
) -> tuple[CoordPaths, MemoryCoordStore, CoordService]:
    paths = make_paths(tmp_path)
    store = MemoryCoordStore()
    return paths, store, CoordService(paths=paths, store=store, now_fn=FakeClock(*timestamps))


def make_sqlite_service(
    tmp_path: Path, *timestamps: str
) -> tuple[CoordPaths, SQLiteCoordStore, CoordService]:
    paths = make_sqlite_paths(tmp_path)
    store = SQLiteCoordStore(paths.control_db)
    return paths, store, CoordService(paths=paths, store=store, now_fn=FakeClock(*timestamps))


def init_default_control_plane(
    service: CoordService,
    milestone: str = DEFAULT_MILESTONE,
    run_date: str = DEFAULT_RUN_DATE,
    roles: tuple[str, ...] = DEFAULT_ROLES,
) -> None:
    service.init_control_plane(milestone, run_date=run_date, roles=roles)


def open_default_gate(
    service: CoordService,
    milestone: str = DEFAULT_MILESTONE,
    phase: str = DEFAULT_PHASE,
    gate_id: str = DEFAULT_GATE_ID,
    allowed_role: str = DEFAULT_ALLOWED_ROLE,
    target_commit: str = DEFAULT_TARGET_COMMIT,
    task: str = "open backend phase 1 gate",
) -> None:
    service.open_gate(
        milestone,
        phase=phase,
        gate_id=gate_id,
        allowed_role=allowed_role,
        target_commit=target_commit,
        task=task,
    )


def ack_default_gate_open(
    service: CoordService,
    milestone: str = DEFAULT_MILESTONE,
    gate_id: str = DEFAULT_GATE_ID,
    commit: str = DEFAULT_TARGET_COMMIT,
    phase: str | None = None,
    task: str = "ACK GATE_OPEN, starting Phase 1",
) -> None:
    kwargs = {"phase": phase} if phase is not None else {}
    service.ack(
        milestone,
        role=DEFAULT_ALLOWED_ROLE,
        command="GATE_OPEN",
        gate_id=gate_id,
        commit=commit,
        task=task,
        **kwargs,
    )


def phase_complete_default(
    service: CoordService,
    milestone: str = DEFAULT_MILESTONE,
    phase: str = DEFAULT_PHASE,
    gate_id: str = DEFAULT_GATE_ID,
    commit: str = "def5678",
    task: str = "Phase 1 complete",
    branch: str | None = None,
) -> None:
    kwargs = {"branch": branch} if branch is not None else {}
    service.phase_complete(
        milestone,
        role=DEFAULT_ALLOWED_ROLE,
        phase=phase,
        gate_id=gate_id,
        commit=commit,
        task=task,
        **kwargs,
    )


def gate_review_default(
    service: CoordService,
    report_commit: str,
    report_path: str,
    milestone: str = DEFAULT_MILESTONE,
    phase: str = DEFAULT_PHASE,
    gate_id: str = DEFAULT_GATE_ID,
    role: str = "tester",
    result: str = "PASS",
    task: str = "Phase 1 review PASS",
) -> None:
    service.gate_review(
        milestone,
        role=role,
        phase=phase,
        gate_id=gate_id,
        result=result,
        report_commit=report_commit,
        report_path=report_path,
        task=task,
    )


def gate_close_default(
    service: CoordService,
    report_commit: str,
    report_path: str,
    milestone: str = DEFAULT_MILESTONE,
    phase: str = DEFAULT_PHASE,
    gate_id: str = DEFAULT_GATE_ID,
    result: str = "PASS",
    task: str = "close gate after review",
) -> None:
    service.gate_close(
        milestone,
        phase=phase,
        gate_id=gate_id,
        result=result,
        report_commit=report_commit,
        report_path=report_path,
        task=task,
    )


def rendered_log_dir(
    paths: CoordPaths,
    milestone: str = DEFAULT_MILESTONE,
    run_date: str = DEFAULT_RUN_DATE,
) -> Path:
    return paths.log_dir(milestone.lower(), run_date)


def read_heartbeat_events(
    paths: CoordPaths,
    milestone: str = DEFAULT_MILESTONE,
    run_date: str = DEFAULT_RUN_DATE,
) -> list[dict[str, object]]:
    lines = (rendered_log_dir(paths, milestone, run_date) / "heartbeat_events.jsonl").read_text(
        "utf-8"
    )
    return [json.loads(line) for line in lines.splitlines() if line.strip()]


def event_names(events: list[dict[str, object]]) -> list[str]:
    return [str(event["event"]) for event in events]


def write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), "utf-8")


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
