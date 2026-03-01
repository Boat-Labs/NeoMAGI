from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

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

    @property
    def lock_file(self) -> Path:
        return self.git_common_dir / "coord.lock"

    def log_dir(self, milestone: str, run_date: str) -> Path:
        return self.workspace_root / "dev_docs" / "logs" / f"{milestone}_{run_date}"

    @property
    def progress_file(self) -> Path:
        return self.workspace_root / "dev_docs" / "progress" / "project_progress.md"


@dataclass
class IssueRecord:
    issue_id: str
    title: str
    description: str
    issue_type: str
    status: str
    labels: tuple[str, ...]
    metadata: dict[str, Any]
    assignee: str | None = None
    parent_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> IssueRecord:
        labels = payload.get("labels") or []
        if isinstance(labels, str):
            labels = [part.strip() for part in labels.split(",") if part.strip()]
        metadata = payload.get("metadata") or payload.get("meta") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except json.JSONDecodeError:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        return cls(
            issue_id=str(payload.get("id") or payload.get("issue_id") or ""),
            title=str(payload.get("title") or ""),
            description=str(payload.get("description") or ""),
            issue_type=str(payload.get("type") or ""),
            status=str(payload.get("status") or "open"),
            labels=tuple(sorted(str(label) for label in labels)),
            metadata=metadata,
            assignee=_optional_str(payload.get("assignee")),
            parent_id=_optional_str(payload.get("parent_id") or payload.get("parent")),
            created_at=_optional_str(payload.get("created_at")),
            updated_at=_optional_str(payload.get("updated_at")),
            closed_at=_optional_str(payload.get("closed_at")),
        )

    def has_label(self, label: str) -> bool:
        return label in self.labels

    def metadata_str(self, key: str, default: str = "") -> str:
        value = self.metadata.get(key, default)
        return _stringify(value, default)

    def metadata_int(self, key: str, default: int = 0) -> int:
        value = self.metadata.get(key, default)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str) and value.strip():
            return int(value)
        return default

    def metadata_bool(self, key: str, default: bool = False) -> bool:
        value = self.metadata.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.lower() == "true"
        return bool(value)


class IssueStore(Protocol):
    def init_repo(self) -> None: ...

    def load_issues(self) -> list[IssueRecord]: ...

    def create_issue(
        self,
        *,
        title: str,
        issue_type: str,
        description: str,
        labels: Sequence[str],
        metadata: dict[str, Any],
        assignee: str | None = None,
        parent_id: str | None = None,
        status: str = "open",
    ) -> str: ...

    def update_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        labels: Sequence[str] | None = None,
        metadata: dict[str, Any] | None = None,
        assignee: str | None = None,
        status: str | None = None,
    ) -> None: ...


class CliIssueStore:
    def __init__(
        self,
        beads_dir: Path,
        *,
        bd_bin: str = "bd",
        dolt_bin: str = "dolt",
    ) -> None:
        self.beads_dir = beads_dir
        self.bd_bin = bd_bin
        self.dolt_bin = dolt_bin

    def init_repo(self) -> None:
        self._ensure_binary(self.bd_bin, "bd")
        self._ensure_binary(self.dolt_bin, "dolt")
        self.beads_dir.mkdir(parents=True, exist_ok=True)
        metadata_file = self.beads_dir / ".beads" / "metadata.json"
        if metadata_file.exists():
            return
        self._run(
            [
                self.bd_bin,
                "init",
                "--quiet",
                "--skip-hooks",
            ]
        )

    def load_issues(self) -> list[IssueRecord]:
        metadata_file = self.beads_dir / ".beads" / "metadata.json"
        if not metadata_file.exists():
            return []
        result = self._run(
            [
                self.bd_bin,
                "list",
                "--all",
                "--include-infra",
                "--json",
            ]
        )
        payload = json.loads(result.stdout or "[]")
        if not isinstance(payload, list):
            raise CoordError("bd list --json returned non-list payload")
        return [IssueRecord.from_mapping(item) for item in payload if isinstance(item, dict)]

    def create_issue(
        self,
        *,
        title: str,
        issue_type: str,
        description: str,
        labels: Sequence[str],
        metadata: dict[str, Any],
        assignee: str | None = None,
        parent_id: str | None = None,
        status: str = "open",
    ) -> str:
        command = [
            self.bd_bin,
            "create",
            "--silent",
            "--no-inherit-labels",
            "--type",
            issue_type,
            "--title",
            title,
            "--description",
            description,
            "--labels",
            ",".join(sorted(set(labels))),
            "--metadata",
            json.dumps(metadata, ensure_ascii=True, sort_keys=True),
        ]
        if assignee:
            command.extend(["--assignee", assignee])
        if parent_id:
            command.extend(["--parent", parent_id])
        issue_id = self._run(command).stdout.strip()
        if not issue_id:
            raise CoordError(f"bd create returned an empty issue id for {title!r}")
        if status != "open":
            self.update_issue(issue_id, status=status)
        return issue_id

    def update_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        labels: Sequence[str] | None = None,
        metadata: dict[str, Any] | None = None,
        assignee: str | None = None,
        status: str | None = None,
    ) -> None:
        command = [self.bd_bin, "update", issue_id]
        if title is not None:
            command.extend(["--title", title])
        if description is not None:
            command.extend(["--description", description])
        if labels is not None:
            for label in sorted(set(labels)):
                command.extend(["--set-labels", label])
        if metadata is not None:
            command.extend(["--metadata", json.dumps(metadata, ensure_ascii=True, sort_keys=True)])
        if assignee is not None:
            command.extend(["--assignee", assignee])
        if status is not None:
            command.extend(["--status", status])
        self._run(command)

    def _run(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                list(command),
                cwd=self.beads_dir,
                capture_output=True,
                check=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise CoordError(f"missing binary while running {' '.join(command)}: {exc}") from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip()
            stdout = exc.stdout.strip()
            message = stderr or stdout or f"command failed: {' '.join(command)}"
            raise CoordError(message) from exc

    @staticmethod
    def _ensure_binary(binary: str, label: str) -> None:
        if _which(binary) is None:
            raise CoordError(
                f"{label} binary not found in PATH. "
                f"Install {label} before running the devcoord control plane."
            )


class MemoryIssueStore:
    def __init__(self) -> None:
        self._issues: dict[str, IssueRecord] = {}
        self._counter = 0

    def init_repo(self) -> None:
        return None

    def load_issues(self) -> list[IssueRecord]:
        return list(self._issues.values())

    def create_issue(
        self,
        *,
        title: str,
        issue_type: str,
        description: str,
        labels: Sequence[str],
        metadata: dict[str, Any],
        assignee: str | None = None,
        parent_id: str | None = None,
        status: str = "open",
    ) -> str:
        self._counter += 1
        issue_id = f"coord-{self._counter}"
        self._issues[issue_id] = IssueRecord(
            issue_id=issue_id,
            title=title,
            description=description,
            issue_type=issue_type,
            status=status,
            labels=tuple(sorted(set(labels))),
            metadata=dict(metadata),
            assignee=assignee,
            parent_id=parent_id,
        )
        return issue_id

    def update_issue(
        self,
        issue_id: str,
        *,
        title: str | None = None,
        description: str | None = None,
        labels: Sequence[str] | None = None,
        metadata: dict[str, Any] | None = None,
        assignee: str | None = None,
        status: str | None = None,
    ) -> None:
        issue = self._issues[issue_id]
        self._issues[issue_id] = IssueRecord(
            issue_id=issue.issue_id,
            title=issue.title if title is None else title,
            description=issue.description if description is None else description,
            issue_type=issue.issue_type,
            status=issue.status if status is None else status,
            labels=issue.labels if labels is None else tuple(sorted(set(labels))),
            metadata=issue.metadata if metadata is None else dict(metadata),
            assignee=issue.assignee if assignee is None else assignee,
            parent_id=issue.parent_id,
            created_at=issue.created_at,
            updated_at=issue.updated_at,
            closed_at=issue.closed_at,
        )



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
