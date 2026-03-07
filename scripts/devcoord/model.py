from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

COORD_LABEL = "coord"
KIND_KEY = "coord_kind"
SCHEMA_VERSION = 1
DEFAULT_ROLES = ("pm", "backend", "tester")
LEGACY_BEADS_SUBDIR = Path(".coord/beads")


class CoordError(RuntimeError):
    """Raised when the coordination control plane cannot complete an operation."""


@dataclass(frozen=True)
class CoordPaths:
    workspace_root: Path
    beads_dir: Path
    git_common_dir: Path
    control_root: Path | None = None

    @property
    def control_db(self) -> Path:
        return (self.control_root or self._default_control_root) / "control.db"

    @property
    def _default_control_root(self) -> Path:
        return self.workspace_root / ".devcoord"

    @property
    def lock_file(self) -> Path:
        root = self.control_root or self._default_control_root
        return root / "coord.lock"

    def phase_subdir(self, milestone: str) -> str:
        normalized = milestone.strip().lower()
        if normalized.startswith("p2-"):
            return "phase2"
        return "phase1"

    def log_dir(self, milestone: str, run_date: str) -> Path:
        return (
            self.workspace_root
            / "dev_docs"
            / "logs"
            / self.phase_subdir(milestone)
            / f"{milestone}_{run_date}"
        )

    @property
    def progress_file(self) -> Path:
        return self.workspace_root / "dev_docs" / "progress" / "project_progress.md"


def _git_output(cwd: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            check=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() or exc.stdout.strip()
        raise CoordError(f"git {' '.join(args)} failed: {stderr}") from exc
    return result.stdout.strip()

def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


def _stringify(value: Any, default: str = "") -> str:
    if value in (None, ""):
        return default
    if isinstance(value, str):
        return value
    return str(value)

def _which(binary: str) -> str | None:
    candidate_path = Path(binary)
    if candidate_path.is_absolute() or candidate_path.parent != Path():
        if candidate_path.exists() and os.access(candidate_path, os.X_OK):
            return str(candidate_path)
        return None
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(entry) / binary
        if candidate.exists() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None
