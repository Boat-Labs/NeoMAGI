from __future__ import annotations

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
    "FakeClock",
    "MemoryCoordStore",
    "SQLITE_SCHEMA_VERSION",
    "SQLiteCoordStore",
    "_normalize_argv",
    "_resolve_paths",
    "build_parser",
    "init_git_repo_with_review",
    "make_paths",
    "make_sqlite_paths",
    "run_cli",
]


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
