from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
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


@dataclass
class CoordService:
    paths: CoordPaths
    store: IssueStore
    now_fn: Callable[[], str] = field(default=lambda: _utc_now())

    def init_control_plane(self, milestone: str, *, run_date: str, roles: Sequence[str]) -> None:
        normalized_milestone = _normalize_milestone(milestone)
        normalized_roles = tuple(_normalize_role(role) for role in roles)
        with self._locked():
            self.store.init_repo()
            issues = self._coord_issues(normalized_milestone)
            milestone_issue = self._find_single(issues, "milestone")
            milestone_metadata = {
                KIND_KEY: "milestone",
                "milestone": normalized_milestone,
                "run_date": run_date,
                "schema_version": SCHEMA_VERSION,
            }
            milestone_labels = self._base_labels("milestone", normalized_milestone)
            if milestone_issue is None:
                self.store.create_issue(
                    title=f"Coord milestone {normalized_milestone}",
                    issue_type="epic",
                    description=f"NeoMAGI devcoord control plane for {normalized_milestone}.",
                    labels=milestone_labels,
                    metadata=milestone_metadata,
                )
            else:
                self.store.update_issue(
                    milestone_issue.issue_id,
                    labels=milestone_labels,
                    metadata=_merge_dicts(milestone_issue.metadata, milestone_metadata),
                )
            issues = self._coord_issues(normalized_milestone)
            milestone_issue = self._require_single(issues, "milestone")
            for role in normalized_roles:
                agent_issue = self._find_single(issues, "agent", role=role)
                agent_metadata = {
                    KIND_KEY: "agent",
                    "milestone": normalized_milestone,
                    "role": role,
                    "agent_state": "idle",
                    "last_activity": "",
                    "current_task": "",
                    "stale_risk": "none",
                    "action": "awaiting gate",
                }
                labels = self._base_labels("agent", normalized_milestone, role=role)
                if agent_issue is None:
                    self.store.create_issue(
                        title=f"Agent {role}",
                        issue_type="task",
                        description=f"Coordination state for {role}.",
                        labels=labels,
                        metadata=agent_metadata,
                        parent_id=milestone_issue.issue_id,
                    )
                    continue
                self.store.update_issue(
                    agent_issue.issue_id,
                    labels=labels,
                    metadata=_merge_dicts(agent_issue.metadata, agent_metadata),
                )

    def open_gate(
        self,
        milestone: str,
        *,
        phase: str,
        gate_id: str,
        allowed_role: str,
        target_commit: str,
        task: str,
    ) -> None:
        normalized_milestone = _normalize_milestone(milestone)
        normalized_role = _normalize_role(allowed_role)
        now = self.now_fn()
        with self._locked():
            self.store.init_repo()
            issues = self._coord_issues(normalized_milestone)
            milestone_issue = self._require_single(issues, "milestone")
            phase_issue = self._ensure_phase(issues, milestone_issue, normalized_milestone, phase)
            issues = self._coord_issues(normalized_milestone)
            gate_issue = self._find_single(issues, "gate", gate_id=gate_id)
            gate_metadata = {
                KIND_KEY: "gate",
                "milestone": normalized_milestone,
                "phase": phase,
                "gate_id": gate_id,
                "allowed_role": normalized_role,
                "target_commit": target_commit,
                "result": "",
                "report_path": "",
                "report_commit": "",
                "gate_state": "pending",
                "opened_at": "",
                "closed_at": "",
            }
            gate_labels = self._base_labels(
                "gate",
                normalized_milestone,
                phase=phase,
                role=normalized_role,
            )
            if gate_issue is None:
                gate_issue_id = self.store.create_issue(
                    title=f"Gate {gate_id}",
                    issue_type="task",
                    description=f"Gate {gate_id} for phase {phase}.",
                    labels=gate_labels,
                    metadata=gate_metadata,
                    parent_id=phase_issue.issue_id,
                )
            else:
                gate_issue_id = gate_issue.issue_id
                self.store.update_issue(
                    gate_issue_id,
                    labels=gate_labels,
                    metadata=_merge_dicts(gate_issue.metadata, gate_metadata),
                )
            message_metadata = {
                KIND_KEY: "message",
                "milestone": normalized_milestone,
                "phase": phase,
                "gate_id": gate_id,
                "role": normalized_role,
                "command": "GATE_OPEN",
                "requires_ack": True,
                "effective": False,
                "target_commit": target_commit,
                "allowed_role": normalized_role,
                "sent_at": now,
                "task": task,
            }
            self.store.create_issue(
                title=f"GATE_OPEN -> {normalized_role}",
                issue_type="task",
                description=task,
                labels=self._base_labels(
                    "message",
                    normalized_milestone,
                    phase=phase,
                    role=normalized_role,
                ),
                metadata=message_metadata,
                assignee=normalized_role,
                parent_id=gate_issue_id,
            )
            issues = self._coord_issues(normalized_milestone)
            self._record_event(
                issues=issues,
                milestone=normalized_milestone,
                phase=phase,
                role="pm",
                status="working",
                task=task,
                event="GATE_OPEN_SENT",
                gate_id=gate_id,
                target_commit=target_commit,
                parent_id=gate_issue_id,
                ts=now,
            )
            issues = self._coord_issues(normalized_milestone)
            self._update_agent(
                issues,
                milestone=normalized_milestone,
                role=normalized_role,
                state="spawning",
                task=task,
                last_activity=now,
                action=f"awaiting ACK for {gate_id}",
            )

    def ack(
        self,
        milestone: str,
        *,
        role: str,
        command: str,
        gate_id: str,
        commit: str,
        phase: str | None = None,
        task: str,
    ) -> None:
        normalized_milestone = _normalize_milestone(milestone)
        normalized_role = _normalize_role(role)
        command_name = command.upper()
        with self._locked():
            issues = self._coord_issues(normalized_milestone)
            gate_issue = self._require_single(issues, "gate", gate_id=gate_id)
            message_issue = self._find_pending_message(
                issues,
                role=normalized_role,
                gate_id=gate_id,
                command=command_name,
            )
            if message_issue is None:
                raise CoordError(
                    f"no pending {command_name} message for role={normalized_role} gate={gate_id}"
                )
            now = self.now_fn()
            resolved_phase = phase or message_issue.metadata_str("phase")
            updated_message_metadata = _merge_dicts(
                message_issue.metadata,
                {
                    "effective": True,
                    "acked_at": now,
                    "ack_role": normalized_role,
                    "ack_commit": commit,
                },
            )
            self.store.update_issue(
                message_issue.issue_id,
                metadata=updated_message_metadata,
            )
            issues = self._coord_issues(normalized_milestone)
            self._record_event(
                issues=issues,
                milestone=normalized_milestone,
                phase=resolved_phase,
                role=normalized_role,
                status="working",
                task=task,
                event="ACK",
                gate_id=gate_id,
                target_commit=commit,
                ack_of=command_name,
                parent_id=gate_issue.issue_id,
                ts=now,
                source_message_id=message_issue.issue_id,
            )
            gate_metadata = _merge_dicts(
                gate_issue.metadata,
                {
                    "gate_state": "open",
                    "opened_at": now,
                    "target_commit": commit,
                },
            )
            self.store.update_issue(gate_issue.issue_id, metadata=gate_metadata)
            issues = self._coord_issues(normalized_milestone)
            self._record_event(
                issues=issues,
                milestone=normalized_milestone,
                phase=resolved_phase,
                role="pm",
                status="working",
                task=f"{command_name} effective for {normalized_role}",
                event="GATE_EFFECTIVE",
                gate_id=gate_id,
                target_commit=commit,
                parent_id=gate_issue.issue_id,
                ts=now,
                source_message_id=message_issue.issue_id,
            )
            issues = self._coord_issues(normalized_milestone)
            self._update_agent(
                issues,
                milestone=normalized_milestone,
                role=normalized_role,
                state="working",
                task=task,
                last_activity=now,
                action=f"gate {gate_id} effective",
            )

    def heartbeat(
        self,
        milestone: str,
        *,
        role: str,
        phase: str,
        status: str,
        task: str,
        eta_min: int | None,
        gate_id: str | None = None,
        target_commit: str | None = None,
        branch: str | None = None,
    ) -> None:
        normalized_milestone = _normalize_milestone(milestone)
        normalized_role = _normalize_role(role)
        now = self.now_fn()
        with self._locked():
            issues = self._coord_issues(normalized_milestone)
            parent_id = self._event_parent_id(issues, phase=phase, gate_id=gate_id)
            self._record_event(
                issues=issues,
                milestone=normalized_milestone,
                phase=phase,
                role=normalized_role,
                status=status,
                task=task,
                event="HEARTBEAT",
                gate_id=gate_id,
                target_commit=target_commit,
                parent_id=parent_id,
                ts=now,
                eta_min=eta_min,
                branch=branch,
            )
            issues = self._coord_issues(normalized_milestone)
            self._update_agent(
                issues,
                milestone=normalized_milestone,
                role=normalized_role,
                state=_to_agent_state(status),
                task=task,
                last_activity=now,
                action="reporting progress",
            )

    def phase_complete(
        self,
        milestone: str,
        *,
        role: str,
        phase: str,
        gate_id: str,
        commit: str,
        task: str,
        branch: str | None = None,
    ) -> None:
        normalized_milestone = _normalize_milestone(milestone)
        normalized_role = _normalize_role(role)
        now = self.now_fn()
        with self._locked():
            issues = self._coord_issues(normalized_milestone)
            gate_issue = self._require_single(issues, "gate", gate_id=gate_id)
            phase_issue = self._require_single(issues, "phase", phase=phase)
            self.store.update_issue(
                gate_issue.issue_id,
                metadata=_merge_dicts(gate_issue.metadata, {"target_commit": commit}),
            )
            self.store.update_issue(
                phase_issue.issue_id,
                metadata=_merge_dicts(
                    phase_issue.metadata,
                    {"phase_state": "submitted", "last_commit": commit},
                ),
            )
            issues = self._coord_issues(normalized_milestone)
            self._record_event(
                issues=issues,
                milestone=normalized_milestone,
                phase=phase,
                role=normalized_role,
                status="done",
                task=task,
                event="PHASE_COMPLETE",
                gate_id=gate_id,
                target_commit=commit,
                parent_id=gate_issue.issue_id,
                ts=now,
                eta_min=0,
                branch=branch,
            )
            issues = self._coord_issues(normalized_milestone)
            self._update_agent(
                issues,
                milestone=normalized_milestone,
                role=normalized_role,
                state="done",
                task=task,
                last_activity=now,
                action=f"waiting for next gate after {gate_id}",
            )

    def gate_review(
        self,
        milestone: str,
        *,
        role: str,
        phase: str,
        gate_id: str,
        result: str,
        report_commit: str,
        report_path: str,
        task: str,
    ) -> None:
        normalized_milestone = _normalize_milestone(milestone)
        normalized_role = _normalize_role(role)
        normalized_result = result.upper()
        now = self.now_fn()
        with self._locked():
            issues = self._coord_issues(normalized_milestone)
            gate_issue = self._require_single(issues, "gate", gate_id=gate_id)
            gate_metadata = _merge_dicts(
                gate_issue.metadata,
                {
                    "result": normalized_result,
                    "report_commit": report_commit,
                    "report_path": report_path,
                },
            )
            self.store.update_issue(gate_issue.issue_id, metadata=gate_metadata)
            issues = self._coord_issues(normalized_milestone)
            self._record_event(
                issues=issues,
                milestone=normalized_milestone,
                phase=phase,
                role=normalized_role,
                status="done",
                task=task,
                event="GATE_REVIEW_COMPLETE",
                gate_id=gate_id,
                target_commit=gate_issue.metadata_str("target_commit"),
                parent_id=gate_issue.issue_id,
                ts=now,
                eta_min=0,
                result=normalized_result,
                report_commit=report_commit,
                report_path=report_path,
            )
            issues = self._coord_issues(normalized_milestone)
            self._update_agent(
                issues,
                milestone=normalized_milestone,
                role=normalized_role,
                state="done",
                task=task,
                last_activity=now,
                action=f"review submitted for {gate_id}",
            )

    def gate_close(
        self,
        milestone: str,
        *,
        phase: str,
        gate_id: str,
        result: str,
        report_commit: str,
        report_path: str,
        task: str,
    ) -> None:
        normalized_milestone = _normalize_milestone(milestone)
        normalized_result = result.upper()
        now = self.now_fn()
        with self._locked():
            issues = self._coord_issues(normalized_milestone)
            gate_issue = self._require_single(issues, "gate", gate_id=gate_id)
            phase_issue = self._require_single(issues, "phase", phase=phase)
            gate_metadata = _merge_dicts(
                gate_issue.metadata,
                {
                    "result": normalized_result,
                    "report_commit": report_commit,
                    "report_path": report_path,
                    "gate_state": "closed",
                    "closed_at": now,
                },
            )
            self.store.update_issue(gate_issue.issue_id, metadata=gate_metadata)
            self.store.update_issue(
                phase_issue.issue_id,
                metadata=_merge_dicts(
                    phase_issue.metadata,
                    {"phase_state": "closed"},
                ),
            )
            issues = self._coord_issues(normalized_milestone)
            self._record_event(
                issues=issues,
                milestone=normalized_milestone,
                phase=phase,
                role="pm",
                status="working",
                task=task,
                event="GATE_CLOSE",
                gate_id=gate_id,
                target_commit=gate_issue.metadata_str("target_commit"),
                parent_id=gate_issue.issue_id,
                ts=now,
                result=normalized_result,
                report_commit=report_commit,
                report_path=report_path,
            )

    def render(self, milestone: str) -> None:
        normalized_milestone = _normalize_milestone(milestone)
        issues = self._coord_issues(normalized_milestone)
        milestone_issue = self._require_single(issues, "milestone")
        run_date = milestone_issue.metadata_str("run_date")
        if not run_date:
            raise CoordError(f"milestone {normalized_milestone} does not have run_date metadata")
        log_dir = self.paths.log_dir(normalized_milestone, run_date)
        log_dir.mkdir(parents=True, exist_ok=True)
        events = sorted(
            self._iter_kind(issues, "event"),
            key=lambda issue: (issue.metadata_int("event_seq"), issue.issue_id),
        )
        heartbeat_events_path = log_dir / "heartbeat_events.jsonl"
        heartbeat_lines = [
            json.dumps(self._event_projection(issue), ensure_ascii=False)
            for issue in events
        ]
        heartbeat_events_path.write_text(
            "\n".join(heartbeat_lines) + ("\n" if heartbeat_lines else ""),
            "utf-8",
        )
        gate_state_path = log_dir / "gate_state.md"
        gate_state_path.write_text(self._render_gate_state(issues, normalized_milestone), "utf-8")
        watchdog_path = log_dir / "watchdog_status.md"
        watchdog_path.write_text(self._render_watchdog(issues, normalized_milestone), "utf-8")

    def _coord_issues(self, milestone: str) -> list[IssueRecord]:
        return [
            issue
            for issue in self.store.load_issues()
            if issue.has_label(COORD_LABEL) and issue.metadata_str("milestone") == milestone
        ]

    def _ensure_phase(
        self,
        issues: Sequence[IssueRecord],
        milestone_issue: IssueRecord,
        milestone: str,
        phase: str,
    ) -> IssueRecord:
        phase_issue = self._find_single(issues, "phase", phase=phase)
        phase_metadata = {
            KIND_KEY: "phase",
            "milestone": milestone,
            "phase": phase,
            "phase_state": "in_progress",
            "last_commit": "",
        }
        phase_labels = self._base_labels("phase", milestone, phase=phase)
        if phase_issue is None:
            phase_issue_id = self.store.create_issue(
                title=f"Coord phase {phase}",
                issue_type="task",
                description=f"Coordination phase {phase} for {milestone}.",
                labels=phase_labels,
                metadata=phase_metadata,
                parent_id=milestone_issue.issue_id,
            )
            return IssueRecord(
                issue_id=phase_issue_id,
                title=f"Coord phase {phase}",
                description="",
                issue_type="task",
                status="open",
                labels=tuple(sorted(phase_labels)),
                metadata=phase_metadata,
                parent_id=milestone_issue.issue_id,
            )
        self.store.update_issue(
            phase_issue.issue_id,
            labels=phase_labels,
            metadata=_merge_dicts(phase_issue.metadata, phase_metadata),
        )
        return IssueRecord(
            issue_id=phase_issue.issue_id,
            title=phase_issue.title,
            description=phase_issue.description,
            issue_type=phase_issue.issue_type,
            status=phase_issue.status,
            labels=tuple(sorted(phase_labels)),
            metadata=_merge_dicts(phase_issue.metadata, phase_metadata),
            assignee=phase_issue.assignee,
            parent_id=phase_issue.parent_id,
            created_at=phase_issue.created_at,
            updated_at=phase_issue.updated_at,
            closed_at=phase_issue.closed_at,
        )

    def _record_event(
        self,
        *,
        issues: Sequence[IssueRecord],
        milestone: str,
        phase: str,
        role: str,
        status: str,
        task: str,
        event: str,
        gate_id: str | None,
        target_commit: str | None,
        parent_id: str | None,
        ts: str,
        ack_of: str | None = None,
        eta_min: int | None = None,
        result: str | None = None,
        branch: str | None = None,
        report_commit: str | None = None,
        report_path: str | None = None,
        source_message_id: str | None = None,
    ) -> str:
        next_seq = self._next_event_seq(issues)
        metadata = {
            KIND_KEY: "event",
            "milestone": milestone,
            "phase": phase,
            "role": role,
            "status": status,
            "task": task,
            "event": event,
            "gate": gate_id or "",
            "target_commit": target_commit or "",
            "event_seq": next_seq,
            "eta_min": eta_min,
            "result": result or "",
            "branch": branch or "",
            "ack_of": ack_of or "",
            "report_commit": report_commit or "",
            "report_path": report_path or "",
            "source_message_id": source_message_id or "",
            "ts": ts,
        }
        return self.store.create_issue(
            title=f"{event} {role} phase {phase}",
            issue_type="task",
            description=task,
            labels=self._base_labels("event", milestone, phase=phase, role=role),
            metadata=metadata,
            parent_id=parent_id,
        )

    def _update_agent(
        self,
        issues: Sequence[IssueRecord],
        *,
        milestone: str,
        role: str,
        state: str,
        task: str,
        last_activity: str,
        action: str,
    ) -> None:
        agent_issue = self._require_single(issues, "agent", role=role)
        agent_metadata = _merge_dicts(
            agent_issue.metadata,
            {
                "agent_state": state,
                "current_task": task,
                "last_activity": last_activity,
                "action": action,
            },
        )
        self.store.update_issue(
            agent_issue.issue_id,
            metadata=agent_metadata,
            labels=self._base_labels("agent", milestone, role=role),
        )

    def _event_parent_id(
        self,
        issues: Sequence[IssueRecord],
        *,
        phase: str,
        gate_id: str | None,
    ) -> str | None:
        if gate_id:
            gate_issue = self._find_single(issues, "gate", gate_id=gate_id)
            if gate_issue is not None:
                return gate_issue.issue_id
        phase_issue = self._find_single(issues, "phase", phase=phase)
        if phase_issue is not None:
            return phase_issue.issue_id
        return None

    def _find_pending_message(
        self,
        issues: Sequence[IssueRecord],
        *,
        role: str,
        gate_id: str,
        command: str,
    ) -> IssueRecord | None:
        candidates = [
            issue
            for issue in self._iter_kind(issues, "message")
            if issue.metadata_str("role") == role
            and issue.metadata_str("gate_id") == gate_id
            and issue.metadata_str("command").upper() == command
            and not issue.metadata_bool("effective")
        ]
        candidates.sort(key=lambda issue: issue.issue_id)
        return candidates[-1] if candidates else None

    @staticmethod
    def _base_labels(
        kind: str,
        milestone: str,
        *,
        phase: str | None = None,
        role: str | None = None,
    ) -> list[str]:
        labels = [
            COORD_LABEL,
            f"coord-kind-{kind}",
            f"coord-milestone-{milestone}",
        ]
        if phase is not None:
            labels.append(f"coord-phase-{phase}")
        if role is not None:
            labels.append(f"coord-role-{role}")
        return labels

    @staticmethod
    def _find_single(
        issues: Sequence[IssueRecord],
        kind: str,
        **matches: str,
    ) -> IssueRecord | None:
        candidates = []
        for issue in issues:
            if issue.metadata_str(KIND_KEY) != kind:
                continue
            if any(issue.metadata_str(key) != value for key, value in matches.items()):
                continue
            candidates.append(issue)
        if not candidates:
            return None
        candidates.sort(key=lambda issue: issue.issue_id)
        return candidates[-1]

    def _require_single(
        self,
        issues: Sequence[IssueRecord],
        kind: str,
        **matches: str,
    ) -> IssueRecord:
        issue = self._find_single(issues, kind, **matches)
        if issue is None:
            filters = ", ".join(f"{key}={value}" for key, value in matches.items())
            raise CoordError(f"missing {kind} issue for {filters or 'control plane'}")
        return issue

    @staticmethod
    def _iter_kind(issues: Sequence[IssueRecord], kind: str) -> Iterable[IssueRecord]:
        return (issue for issue in issues if issue.metadata_str(KIND_KEY) == kind)

    @staticmethod
    def _next_event_seq(issues: Sequence[IssueRecord]) -> int:
        max_seq = 0
        for issue in issues:
            if issue.metadata_str(KIND_KEY) != "event":
                continue
            max_seq = max(max_seq, issue.metadata_int("event_seq"))
        return max_seq + 1

    @staticmethod
    def _event_projection(issue: IssueRecord) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ts": issue.metadata_str("ts"),
            "role": _render_role(issue.metadata_str("role")),
            "phase": issue.metadata_str("phase"),
            "status": issue.metadata_str("status"),
            "task": issue.metadata_str("task"),
            "event": issue.metadata_str("event"),
            "gate": issue.metadata_str("gate"),
            "target_commit": issue.metadata_str("target_commit"),
            "event_seq": issue.metadata_int("event_seq"),
            "eta_min": issue.metadata.get("eta_min"),
        }
        ack_of = issue.metadata_str("ack_of")
        if ack_of:
            payload["ack_of"] = ack_of
        branch = issue.metadata_str("branch")
        if branch:
            payload["branch"] = branch
        result = issue.metadata_str("result")
        if result:
            payload["result"] = result
        report_commit = issue.metadata_str("report_commit")
        if report_commit:
            payload["report_commit"] = report_commit
        report_path = issue.metadata_str("report_path")
        if report_path:
            payload["report_path"] = report_path
        source_message_id = issue.metadata_str("source_message_id")
        if source_message_id:
            payload["source_msg_id"] = source_message_id
        return payload

    def _render_gate_state(self, issues: Sequence[IssueRecord], milestone: str) -> str:
        lines = [
            f"# {milestone.upper()} Gate State",
            "",
            "| Gate | Phase | Status | Result | Opened | Closed | Target Commit | Report |",
            "|------|-------|--------|--------|--------|--------|---------------|--------|",
        ]
        gates = sorted(
            self._iter_kind(issues, "gate"),
            key=lambda issue: (
                _phase_sort_key(issue.metadata_str("phase")),
                issue.metadata_str("gate_id"),
            ),
        )
        for gate in gates:
            report_path = gate.metadata_str("report_path")
            report_commit = gate.metadata_str("report_commit")
            report = ""
            if report_path and report_commit:
                report = f"{report_path} ({report_commit})"
            elif report_path:
                report = report_path
            opened = gate.metadata_str("opened_at")
            closed = gate.metadata_str("closed_at")
            result = gate.metadata_str("result")
            status = gate.metadata_str("gate_state", "pending")
            lines.append(
                "| "
                + " | ".join(
                    [
                        gate.metadata_str("gate_id"),
                        gate.metadata_str("phase"),
                        status,
                        result or "",
                        opened,
                        closed,
                        gate.metadata_str("target_commit"),
                        report,
                    ]
                )
                + " |"
            )
        return "\n".join(lines) + "\n"

    def _render_watchdog(self, issues: Sequence[IssueRecord], milestone: str) -> str:
        lines = [
            f"# {milestone.upper()} Watchdog Status",
            "",
            "| role | status | last_heartbeat | current_task | stale_risk | action |",
            "|------|--------|----------------|--------------|------------|--------|",
        ]
        agents = sorted(
            (
                issue
                for issue in self._iter_kind(issues, "agent")
                if issue.metadata_str("role") != "pm"
            ),
            key=lambda issue: issue.metadata_str("role"),
        )
        if not agents:
            agents = sorted(
                self._iter_kind(issues, "agent"),
                key=lambda issue: issue.metadata_str("role"),
            )
        for agent in agents:
            lines.append(
                "| "
                + " | ".join(
                    [
                        agent.metadata_str("role"),
                        agent.metadata_str("agent_state"),
                        agent.metadata_str("last_activity"),
                        agent.metadata_str("current_task"),
                        agent.metadata_str("stale_risk", "none"),
                        agent.metadata_str("action"),
                    ]
                )
                + " |"
            )
        return "\n".join(lines) + "\n"

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        self.paths.beads_dir.mkdir(parents=True, exist_ok=True)
        self.paths.lock_file.touch(exist_ok=True)
        with self.paths.lock_file.open("r+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NeoMAGI devcoord control plane wrapper")
    parser.add_argument(
        "--beads-dir",
        default=os.environ.get("BEADS_DIR"),
        help="Shared BEADS_DIR. Defaults to the shared repo root containing .beads",
    )
    parser.add_argument(
        "--bd-bin",
        default=os.environ.get("COORD_BD_BIN", "bd"),
        help="Path to bd binary",
    )
    parser.add_argument(
        "--dolt-bin",
        default=os.environ.get("COORD_DOLT_BIN", "dolt"),
        help="Path to dolt binary",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize the shared control plane")
    init_parser.add_argument("--milestone", required=True)
    init_parser.add_argument("--run-date", default=date.today().isoformat())
    init_parser.add_argument("--roles", default=",".join(DEFAULT_ROLES))

    open_gate_parser = subparsers.add_parser("open-gate", help="Create a pending GATE_OPEN command")
    open_gate_parser.add_argument("--milestone", required=True)
    open_gate_parser.add_argument("--phase", required=True)
    open_gate_parser.add_argument("--gate", required=True)
    open_gate_parser.add_argument("--allowed-role", required=True)
    open_gate_parser.add_argument("--target-commit", required=True)
    open_gate_parser.add_argument(
        "--task",
        default="gate open pending",
    )

    ack_parser = subparsers.add_parser("ack", help="ACK a pending command and mark it effective")
    ack_parser.add_argument("--milestone", required=True)
    ack_parser.add_argument("--role", required=True)
    ack_parser.add_argument("--cmd", required=True)
    ack_parser.add_argument("--gate", required=True)
    ack_parser.add_argument("--commit", required=True)
    ack_parser.add_argument("--phase")
    ack_parser.add_argument("--task", default="ACK command")

    heartbeat_parser = subparsers.add_parser("heartbeat", help="Record a heartbeat event")
    heartbeat_parser.add_argument("--milestone", required=True)
    heartbeat_parser.add_argument("--role", required=True)
    heartbeat_parser.add_argument("--phase", required=True)
    heartbeat_parser.add_argument("--status", required=True)
    heartbeat_parser.add_argument("--task", required=True)
    heartbeat_parser.add_argument("--eta-min", type=int)
    heartbeat_parser.add_argument("--gate")
    heartbeat_parser.add_argument("--target-commit")
    heartbeat_parser.add_argument("--branch")

    phase_complete_parser = subparsers.add_parser(
        "phase-complete",
        help="Record a PHASE_COMPLETE event",
    )
    phase_complete_parser.add_argument("--milestone", required=True)
    phase_complete_parser.add_argument("--role", required=True)
    phase_complete_parser.add_argument("--phase", required=True)
    phase_complete_parser.add_argument("--gate", required=True)
    phase_complete_parser.add_argument("--commit", required=True)
    phase_complete_parser.add_argument("--task", required=True)
    phase_complete_parser.add_argument("--branch")

    gate_review_parser = subparsers.add_parser(
        "gate-review",
        help="Record a GATE_REVIEW_COMPLETE event",
    )
    gate_review_parser.add_argument("--milestone", required=True)
    gate_review_parser.add_argument("--role", required=True)
    gate_review_parser.add_argument("--phase", required=True)
    gate_review_parser.add_argument("--gate", required=True)
    gate_review_parser.add_argument("--result", required=True)
    gate_review_parser.add_argument("--report-commit", required=True)
    gate_review_parser.add_argument("--report-path", required=True)
    gate_review_parser.add_argument("--task", required=True)

    gate_close_parser = subparsers.add_parser(
        "gate-close",
        help="Close a gate after review is complete",
    )
    gate_close_parser.add_argument("--milestone", required=True)
    gate_close_parser.add_argument("--phase", required=True)
    gate_close_parser.add_argument("--gate", required=True)
    gate_close_parser.add_argument("--result", required=True)
    gate_close_parser.add_argument("--report-commit", required=True)
    gate_close_parser.add_argument("--report-path", required=True)
    gate_close_parser.add_argument("--task", required=True)

    apply_parser = subparsers.add_parser(
        "apply",
        help="Execute a control-plane action from structured JSON payload",
    )
    apply_parser.add_argument(
        "action",
        choices=(
            "init",
            "open-gate",
            "ack",
            "heartbeat",
            "phase-complete",
            "gate-review",
            "gate-close",
            "render",
        ),
    )
    apply_group = apply_parser.add_mutually_exclusive_group(required=True)
    apply_group.add_argument("--payload-file")
    apply_group.add_argument("--payload-stdin", action="store_true")

    render_parser = subparsers.add_parser("render", help="Render dev_docs projection files")
    render_parser.add_argument("--milestone", required=True)
    return parser


def _execute_action(service: CoordService, command: str, payload: dict[str, Any]) -> None:
    if command == "init":
        roles = payload.get("roles", DEFAULT_ROLES)
        if isinstance(roles, str):
            roles = _split_csv(roles)
        service.init_control_plane(
            _require_payload_str(payload, "milestone"),
            run_date=_payload_str(payload, "run_date", date.today().isoformat()),
            roles=tuple(str(role).strip() for role in roles),
        )
        return
    if command == "open-gate":
        service.open_gate(
            _require_payload_str(payload, "milestone"),
            phase=_require_payload_str(payload, "phase"),
            gate_id=_payload_alias(payload, "gate_id", "gate"),
            allowed_role=_require_payload_str(payload, "allowed_role"),
            target_commit=_require_payload_str(payload, "target_commit"),
            task=_require_payload_str(payload, "task"),
        )
        return
    if command == "ack":
        service.ack(
            _require_payload_str(payload, "milestone"),
            role=_require_payload_str(payload, "role"),
            command=_payload_alias(payload, "command", "cmd"),
            gate_id=_payload_alias(payload, "gate_id", "gate"),
            commit=_require_payload_str(payload, "commit"),
            phase=_payload_str(payload, "phase", None),
            task=_require_payload_str(payload, "task"),
        )
        return
    if command == "heartbeat":
        eta_min = payload.get("eta_min")
        if eta_min is not None:
            eta_min = int(eta_min)
        service.heartbeat(
            _require_payload_str(payload, "milestone"),
            role=_require_payload_str(payload, "role"),
            phase=_require_payload_str(payload, "phase"),
            status=_require_payload_str(payload, "status"),
            task=_require_payload_str(payload, "task"),
            eta_min=eta_min,
            gate_id=_payload_str(payload, "gate_id", _payload_str(payload, "gate", None)),
            target_commit=_payload_str(payload, "target_commit", None),
            branch=_payload_str(payload, "branch", None),
        )
        return
    if command == "phase-complete":
        service.phase_complete(
            _require_payload_str(payload, "milestone"),
            role=_require_payload_str(payload, "role"),
            phase=_require_payload_str(payload, "phase"),
            gate_id=_payload_alias(payload, "gate_id", "gate"),
            commit=_require_payload_str(payload, "commit"),
            task=_require_payload_str(payload, "task"),
            branch=_payload_str(payload, "branch", None),
        )
        return
    if command == "gate-review":
        service.gate_review(
            _require_payload_str(payload, "milestone"),
            role=_require_payload_str(payload, "role"),
            phase=_require_payload_str(payload, "phase"),
            gate_id=_payload_alias(payload, "gate_id", "gate"),
            result=_require_payload_str(payload, "result"),
            report_commit=_require_payload_str(payload, "report_commit"),
            report_path=_require_payload_str(payload, "report_path"),
            task=_require_payload_str(payload, "task"),
        )
        return
    if command == "gate-close":
        service.gate_close(
            _require_payload_str(payload, "milestone"),
            phase=_require_payload_str(payload, "phase"),
            gate_id=_payload_alias(payload, "gate_id", "gate"),
            result=_require_payload_str(payload, "result"),
            report_commit=_require_payload_str(payload, "report_commit"),
            report_path=_require_payload_str(payload, "report_path"),
            task=_require_payload_str(payload, "task"),
        )
        return
    if command == "render":
        service.render(_require_payload_str(payload, "milestone"))
        return
    raise CoordError(f"unsupported action: {command}")


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    store: IssueStore | None = None,
    paths: CoordPaths | None = None,
    now_fn: Callable[[], str] | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    resolved_paths = paths or _resolve_paths(args.beads_dir)
    resolved_store = store or CliIssueStore(
        resolved_paths.beads_dir,
        bd_bin=args.bd_bin,
        dolt_bin=args.dolt_bin,
    )
    service = CoordService(
        paths=resolved_paths,
        store=resolved_store,
        now_fn=now_fn or _utc_now,
    )
    try:
        if args.command == "apply":
            _execute_action(service, args.action, _load_payload(args))
        elif args.command == "init":
            _execute_action(
                service,
                "init",
                {
                    "milestone": args.milestone,
                    "run_date": args.run_date,
                    "roles": _split_csv(args.roles),
                },
            )
        elif args.command == "open-gate":
            _execute_action(
                service,
                "open-gate",
                {
                    "milestone": args.milestone,
                    "phase": args.phase,
                    "gate_id": args.gate,
                    "allowed_role": args.allowed_role,
                    "target_commit": args.target_commit,
                    "task": args.task,
                },
            )
        elif args.command == "ack":
            _execute_action(
                service,
                "ack",
                {
                    "milestone": args.milestone,
                    "role": args.role,
                    "command": args.cmd,
                    "gate_id": args.gate,
                    "commit": args.commit,
                    "phase": args.phase,
                    "task": args.task,
                },
            )
        elif args.command == "heartbeat":
            _execute_action(
                service,
                "heartbeat",
                {
                    "milestone": args.milestone,
                    "role": args.role,
                    "phase": args.phase,
                    "status": args.status,
                    "task": args.task,
                    "eta_min": args.eta_min,
                    "gate_id": _none_if_placeholder(args.gate),
                    "target_commit": _none_if_placeholder(args.target_commit),
                    "branch": _none_if_placeholder(args.branch),
                },
            )
        elif args.command == "phase-complete":
            _execute_action(
                service,
                "phase-complete",
                {
                    "milestone": args.milestone,
                    "role": args.role,
                    "phase": args.phase,
                    "gate_id": args.gate,
                    "commit": args.commit,
                    "task": args.task,
                    "branch": _none_if_placeholder(args.branch),
                },
            )
        elif args.command == "gate-review":
            _execute_action(
                service,
                "gate-review",
                {
                    "milestone": args.milestone,
                    "role": args.role,
                    "phase": args.phase,
                    "gate_id": args.gate,
                    "result": args.result,
                    "report_commit": args.report_commit,
                    "report_path": args.report_path,
                    "task": args.task,
                },
            )
        elif args.command == "gate-close":
            _execute_action(
                service,
                "gate-close",
                {
                    "milestone": args.milestone,
                    "phase": args.phase,
                    "gate_id": args.gate,
                    "result": args.result,
                    "report_commit": args.report_commit,
                    "report_path": args.report_path,
                    "task": args.task,
                },
            )
        elif args.command == "render":
            _execute_action(service, "render", {"milestone": args.milestone})
        else:
            parser.error(f"unknown command: {args.command}")
    except CoordError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    return run_cli()


def _resolve_paths(beads_dir_override: str | None) -> CoordPaths:
    git_common_dir = _resolve_git_common_dir(Path.cwd())
    workspace_root = _shared_workspace_root(Path.cwd())
    if beads_dir_override:
        beads_dir = Path(beads_dir_override).expanduser()
    else:
        root_beads = workspace_root / ".beads" / "metadata.json"
        legacy_beads_root = workspace_root / LEGACY_BEADS_SUBDIR
        legacy_beads = legacy_beads_root / ".beads" / "metadata.json"
        if legacy_beads.exists() and not root_beads.exists():
            raise CoordError(
                "legacy shared control plane detected at .coord/beads; "
                "migrate to repo root .beads or pass --beads-dir explicitly"
            )
        beads_dir = workspace_root
    if not beads_dir.is_absolute():
        beads_dir = (workspace_root / beads_dir).resolve()
    return CoordPaths(
        workspace_root=workspace_root,
        beads_dir=beads_dir,
        git_common_dir=git_common_dir,
    )


def _shared_workspace_root(cwd: Path) -> Path:
    common_path = _resolve_git_common_dir(cwd)
    if common_path.name == ".git":
        return common_path.parent.resolve()
    toplevel = _git_output(cwd, "rev-parse", "--show-toplevel")
    return Path(toplevel).resolve()


def _resolve_git_common_dir(cwd: Path) -> Path:
    common_dir = _git_output(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    return Path(common_dir)


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


def _normalize_milestone(value: str) -> str:
    return value.strip().lower()


def _normalize_role(value: str) -> str:
    return value.strip().lower()


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "payload_file", None):
        raw = Path(args.payload_file).read_text("utf-8")
    elif getattr(args, "payload_stdin", False):
        raw = sys.stdin.read()
    else:
        raise CoordError("structured payload requires --payload-file or --payload-stdin")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CoordError(f"invalid JSON payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise CoordError("payload must be a JSON object")
    return payload


def _require_payload_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if value in (None, ""):
        raise CoordError(f"payload missing required field: {key}")
    return str(value)


def _payload_str(payload: dict[str, Any], key: str, default: str | None) -> str | None:
    value = payload.get(key, default)
    if value in (None, ""):
        return default
    return str(value)


def _payload_alias(payload: dict[str, Any], primary: str, alias: str) -> str:
    value = payload.get(primary)
    if value in (None, ""):
        value = payload.get(alias)
    if value in (None, ""):
        raise CoordError(f"payload missing required field: {primary}")
    return str(value)


def _none_if_placeholder(value: str | None) -> str | None:
    if value is None:
        return None
    if value.strip().lower() in {"", "-", "na", "none", "null"}:
        return None
    return value


def _merge_dicts(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    merged.update(updates)
    return merged


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


def _phase_sort_key(value: str) -> tuple[int, str]:
    if value.isdigit():
        return (0, f"{int(value):08d}")
    return (1, value)


def _render_role(role: str) -> str:
    return "PM" if role == "pm" else role


def _to_agent_state(status: str) -> str:
    normalized = status.strip().lower()
    valid = {"idle", "spawning", "running", "working", "stuck", "done", "stopped", "dead"}
    if normalized in valid:
        return normalized
    return "working"


def _utc_now() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


if __name__ == "__main__":
    raise SystemExit(main())
