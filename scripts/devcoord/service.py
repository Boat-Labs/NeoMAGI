from __future__ import annotations

import contextlib
import fcntl
import json
import re
import subprocess
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .model import (
    COORD_LABEL,
    KIND_KEY,
    SCHEMA_VERSION,
    CoordError,
    CoordPaths,
    IssueRecord,
    IssueStore,
    _git_output,
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
        canonical_target_commit = self._canonicalize_commit_ref(target_commit)
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
                "target_commit": canonical_target_commit,
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
                "target_commit": canonical_target_commit,
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
                target_commit=canonical_target_commit,
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
        canonical_commit = self._canonicalize_commit_ref(commit)
        with self._locked():
            issues = self._coord_issues(normalized_milestone)
            gate_issue = self._require_single(issues, "gate", gate_id=gate_id)
            resolved_phase = phase or gate_issue.metadata_str("phase")
            duplicate_ack = self._find_latest_event(
                issues,
                event="ACK",
                role=normalized_role,
                gate=gate_id,
                phase=resolved_phase,
                ack_of=command_name,
                target_commit=canonical_commit,
            )
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
            updated_message_metadata = _merge_dicts(
                message_issue.metadata,
                {
                    "effective": True,
                    "acked_at": now,
                    "ack_role": normalized_role,
                    "ack_commit": canonical_commit,
                },
            )
            self.store.update_issue(
                message_issue.issue_id,
                metadata=updated_message_metadata,
            )
            if duplicate_ack is not None:
                self.store.update_issue(
                    gate_issue.issue_id,
                    metadata=_merge_dicts(
                        gate_issue.metadata,
                        {
                            "gate_state": "open",
                            "opened_at": gate_issue.metadata_str("opened_at")
                            or duplicate_ack.metadata_str("ts"),
                            "target_commit": canonical_commit,
                        },
                    ),
                )
                issues = self._coord_issues(normalized_milestone)
                self._update_agent(
                    issues,
                    milestone=normalized_milestone,
                    role=normalized_role,
                    state="working",
                    task=duplicate_ack.metadata_str("task") or task,
                    last_activity=now,
                    action=f"gate {gate_id} effective",
                    stale_risk="none",
                )
                return
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
                target_commit=canonical_commit,
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
                    "target_commit": canonical_commit,
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
                target_commit=canonical_commit,
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
                stale_risk="none",
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
        canonical_target_commit = self._canonicalize_commit_ref(target_commit)
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
                target_commit=canonical_target_commit,
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
                stale_risk="none",
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
        canonical_commit = self._canonicalize_commit_ref(commit)
        with self._locked():
            issues = self._coord_issues(normalized_milestone)
            duplicate_phase_complete = self._find_latest_event(
                issues,
                event="PHASE_COMPLETE",
                role=normalized_role,
                gate=gate_id,
                phase=phase,
                target_commit=canonical_commit,
            )
            if duplicate_phase_complete is not None:
                return
            now = self.now_fn()
            gate_issue = self._require_single(issues, "gate", gate_id=gate_id)
            phase_issue = self._require_single(issues, "phase", phase=phase)
            self.store.update_issue(
                gate_issue.issue_id,
                metadata=_merge_dicts(gate_issue.metadata, {"target_commit": canonical_commit}),
            )
            self.store.update_issue(
                phase_issue.issue_id,
                metadata=_merge_dicts(
                    phase_issue.metadata,
                    {"phase_state": "submitted", "last_commit": canonical_commit},
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
                target_commit=canonical_commit,
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
                stale_risk="none",
            )

    def recovery_check(
        self,
        milestone: str,
        *,
        role: str,
        last_seen_gate: str,
        task: str,
    ) -> None:
        normalized_milestone = _normalize_milestone(milestone)
        normalized_role = _normalize_role(role)
        normalized_last_seen_gate = last_seen_gate.strip() or "unknown"
        requested_gate = _none_if_placeholder(normalized_last_seen_gate)
        with self._locked():
            issues = self._coord_issues(normalized_milestone)
            snapshot = self._state_snapshot(issues, preferred_gate_id=requested_gate)
            duplicate_recovery = self._find_latest_event(
                issues,
                event="RECOVERY_CHECK",
                role=normalized_role,
                gate=snapshot["gate_id"],
                phase=snapshot["phase"],
                target_commit=snapshot["target_commit"],
                last_seen_gate=normalized_last_seen_gate,
            )
            if duplicate_recovery is not None:
                return
            now = self.now_fn()
            self._record_event(
                issues=issues,
                milestone=normalized_milestone,
                phase=snapshot["phase"],
                role=normalized_role,
                status="stuck",
                task=task,
                event="RECOVERY_CHECK",
                gate_id=snapshot["gate_id"],
                target_commit=snapshot["target_commit"],
                parent_id=snapshot["parent_id"],
                ts=now,
                last_seen_gate=normalized_last_seen_gate,
                allowed_role=snapshot["allowed_role"],
            )
            action = "awaiting state sync from PM"
            if snapshot["gate_id"]:
                action = f"awaiting state sync for {snapshot['gate_id']}"
            self._update_agent(
                issues=self._coord_issues(normalized_milestone),
                milestone=normalized_milestone,
                role=normalized_role,
                state="stuck",
                task=task,
                last_activity=now,
                action=action,
            )

    def state_sync_ok(
        self,
        milestone: str,
        *,
        role: str,
        gate_id: str,
        target_commit: str,
        task: str,
    ) -> None:
        normalized_milestone = _normalize_milestone(milestone)
        normalized_role = _normalize_role(role)
        canonical_target_commit = self._canonicalize_commit_ref(target_commit)
        now = self.now_fn()
        with self._locked():
            issues = self._coord_issues(normalized_milestone)
            gate_issue = self._require_single(issues, "gate", gate_id=gate_id)
            current_target_commit = gate_issue.metadata_str("target_commit")
            if current_target_commit and canonical_target_commit != current_target_commit:
                raise CoordError(
                    "state sync target_commit mismatch: "
                    f"gate={gate_id} expected={current_target_commit} got={canonical_target_commit}"
                )
            resolved_target_commit = current_target_commit or canonical_target_commit
            phase = gate_issue.metadata_str("phase")
            allowed_role = gate_issue.metadata_str("allowed_role")
            self._record_event(
                issues=issues,
                milestone=normalized_milestone,
                phase=phase,
                role="pm",
                status="working",
                task=task,
                event="STATE_SYNC_OK",
                gate_id=gate_id,
                target_commit=resolved_target_commit,
                parent_id=gate_issue.issue_id,
                ts=now,
                sync_role=normalized_role,
                allowed_role=allowed_role,
            )
            commit_suffix = f" ({resolved_target_commit})" if resolved_target_commit else ""
            self._update_agent(
                issues=self._coord_issues(normalized_milestone),
                milestone=normalized_milestone,
                role=normalized_role,
                state="idle",
                task=task,
                last_activity=now,
                action=f"resume at {gate_id}{commit_suffix}",
                stale_risk="none",
            )

    def stale_detected(
        self,
        milestone: str,
        *,
        role: str,
        phase: str,
        task: str,
        gate_id: str | None = None,
        target_commit: str | None = None,
        ping_count: int | None = None,
    ) -> None:
        normalized_milestone = _normalize_milestone(milestone)
        normalized_role = _normalize_role(role)
        canonical_target_commit = self._canonicalize_commit_ref(target_commit)
        now = self.now_fn()
        with self._locked():
            issues = self._coord_issues(normalized_milestone)
            parent_id = self._event_parent_id(issues, phase=phase, gate_id=gate_id)
            self._record_event(
                issues=issues,
                milestone=normalized_milestone,
                phase=phase,
                role=normalized_role,
                status="stuck",
                task=task,
                event="STALE_DETECTED",
                gate_id=gate_id,
                target_commit=canonical_target_commit,
                parent_id=parent_id,
                ts=now,
                ping_count=ping_count,
            )
            action = "stale detected; investigate and recover"
            if gate_id:
                action = f"stale detected on {gate_id}; investigate and recover"
            self._update_agent(
                issues=self._coord_issues(normalized_milestone),
                milestone=normalized_milestone,
                role=normalized_role,
                state="stuck",
                task=task,
                last_activity=now,
                action=action,
                stale_risk="suspected_stale",
            )

    def ping(
        self,
        milestone: str,
        *,
        role: str,
        phase: str,
        gate_id: str,
        task: str,
        target_commit: str | None = None,
    ) -> None:
        normalized_milestone = _normalize_milestone(milestone)
        normalized_role = _normalize_role(role)
        canonical_target_commit = self._canonicalize_commit_ref(target_commit)
        now = self.now_fn()
        with self._locked():
            issues = self._coord_issues(normalized_milestone)
            gate_issue = self._require_single(issues, "gate", gate_id=gate_id)
            resolved_target_commit = canonical_target_commit or gate_issue.metadata_str(
                "target_commit"
            )
            self.store.create_issue(
                title=f"PING -> {normalized_role}",
                issue_type="task",
                description=task,
                labels=self._base_labels(
                    "message",
                    normalized_milestone,
                    phase=phase,
                    role=normalized_role,
                ),
                metadata={
                    KIND_KEY: "message",
                    "milestone": normalized_milestone,
                    "phase": phase,
                    "gate_id": gate_id,
                    "role": normalized_role,
                    "command": "PING",
                    "requires_ack": True,
                    "effective": False,
                    "target_commit": resolved_target_commit,
                    "allowed_role": normalized_role,
                    "sent_at": now,
                    "task": task,
                },
                assignee=normalized_role,
                parent_id=gate_issue.issue_id,
            )
            self._record_event(
                issues=self._coord_issues(normalized_milestone),
                milestone=normalized_milestone,
                phase=phase,
                role="pm",
                status="working",
                task=task,
                event="PING_SENT",
                gate_id=gate_id,
                target_commit=resolved_target_commit,
                parent_id=gate_issue.issue_id,
                ts=now,
                target_role=normalized_role,
            )

    def unconfirmed_instruction(
        self,
        milestone: str,
        *,
        role: str,
        command: str,
        phase: str,
        gate_id: str,
        task: str,
        target_commit: str | None = None,
        ping_count: int | None = None,
    ) -> None:
        normalized_milestone = _normalize_milestone(milestone)
        normalized_role = _normalize_role(role)
        command_name = command.upper()
        canonical_target_commit = self._canonicalize_commit_ref(target_commit)
        now = self.now_fn()
        with self._locked():
            issues = self._coord_issues(normalized_milestone)
            gate_issue = self._require_single(issues, "gate", gate_id=gate_id)
            resolved_target_commit = canonical_target_commit or gate_issue.metadata_str(
                "target_commit"
            )
            self._record_event(
                issues=issues,
                milestone=normalized_milestone,
                phase=phase,
                role="pm",
                status="working",
                task=task,
                event="UNCONFIRMED_INSTRUCTION",
                gate_id=gate_id,
                target_commit=resolved_target_commit,
                parent_id=gate_issue.issue_id,
                ts=now,
                command_name=command_name,
                target_role=normalized_role,
                ping_count=ping_count,
            )

    def log_pending(
        self,
        milestone: str,
        *,
        phase: str,
        task: str,
        gate_id: str | None = None,
        target_commit: str | None = None,
    ) -> None:
        normalized_milestone = _normalize_milestone(milestone)
        canonical_target_commit = self._canonicalize_commit_ref(target_commit)
        now = self.now_fn()
        with self._locked():
            issues = self._coord_issues(normalized_milestone)
            parent_id = self._event_parent_id(issues, phase=phase, gate_id=gate_id)
            self._record_event(
                issues=issues,
                milestone=normalized_milestone,
                phase=phase,
                role="pm",
                status="blocked",
                task=task,
                event="LOG_PENDING",
                gate_id=gate_id,
                target_commit=canonical_target_commit,
                parent_id=parent_id,
                ts=now,
            )

    def audit(self, milestone: str) -> dict[str, Any]:
        normalized_milestone = _normalize_milestone(milestone)
        issues = self._coord_issues(normalized_milestone)
        milestone_issue = self._require_single(issues, "milestone")
        run_date = milestone_issue.metadata_str("run_date")
        if not run_date:
            raise CoordError(f"milestone {normalized_milestone} does not have run_date metadata")
        events = sorted(
            self._iter_kind(issues, "event"),
            key=lambda issue: (issue.metadata_int("event_seq"), issue.issue_id),
        )
        heartbeat_path = (
            self.paths.log_dir(normalized_milestone, run_date) / "heartbeat_events.jsonl"
        )
        logged_events = 0
        latest_logged_event_seq = 0
        if heartbeat_path.exists():
            for line in heartbeat_path.read_text("utf-8").splitlines():
                if not line.strip():
                    continue
                logged_events += 1
                payload = json.loads(line)
                if isinstance(payload, dict):
                    latest_logged_event_seq = max(
                        latest_logged_event_seq, int(payload.get("event_seq") or 0)
                    )
        gate_states = {
            issue.metadata_str("gate_id"): issue.metadata_str("gate_state", "pending")
            for issue in self._iter_kind(issues, "gate")
        }
        pending_ack_messages = sorted(
            [
                {
                    "command": issue.metadata_str("command"),
                    "role": issue.metadata_str("role"),
                    "gate": issue.metadata_str("gate_id"),
                    "phase": issue.metadata_str("phase"),
                    "target_commit": issue.metadata_str("target_commit"),
                }
                for issue in self._iter_kind(issues, "message")
                if issue.metadata_bool("requires_ack")
                and not issue.metadata_bool("effective")
                and gate_states.get(issue.metadata_str("gate_id"), "pending") != "closed"
            ],
            key=lambda payload: (
                payload["phase"],
                payload["gate"],
                payload["role"],
                payload["command"],
            ),
        )
        open_gates = sorted(
            [
                {
                    "gate": issue.metadata_str("gate_id"),
                    "phase": issue.metadata_str("phase"),
                    "status": issue.metadata_str("gate_state"),
                    "allowed_role": issue.metadata_str("allowed_role"),
                    "target_commit": issue.metadata_str("target_commit"),
                }
                for issue in self._iter_kind(issues, "gate")
                if issue.metadata_str("gate_state") != "closed"
            ],
            key=lambda payload: (payload["phase"], payload["gate"]),
        )
        log_pending_events = [
            self._event_projection(issue)
            for issue in events
            if issue.metadata_str("event") == "LOG_PENDING"
        ]
        latest_event_seq = events[-1].metadata_int("event_seq") if events else 0
        return {
            "milestone": normalized_milestone,
            "run_date": run_date,
            "received_events": len(events),
            "logged_events": logged_events,
            "latest_event_seq": latest_event_seq,
            "latest_logged_event_seq": latest_logged_event_seq,
            "reconciled": len(events) == logged_events,
            "pending_ack_messages": pending_ack_messages,
            "open_gates": open_gates,
            "log_pending_events": log_pending_events,
            "projection_path": str(heartbeat_path.relative_to(self.paths.workspace_root)),
        }

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
                stale_risk="none",
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
            milestone_issue = self._require_single(issues, "milestone")
            gate_issue = self._require_single(issues, "gate", gate_id=gate_id)
            phase_issue = self._require_single(issues, "phase", phase=phase)
            self._ensure_gate_close_guards(
                issues=issues,
                milestone_issue=milestone_issue,
                gate_issue=gate_issue,
                phase=phase,
                result=normalized_result,
                report_commit=report_commit,
                report_path=report_path,
            )
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
        self.paths.progress_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.progress_file.write_text(
            self._render_project_progress(
                milestone=normalized_milestone,
                run_date=run_date,
                issues=issues,
                existing=self.paths.progress_file.read_text("utf-8")
                if self.paths.progress_file.exists()
                else "",
            ),
            "utf-8",
        )

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
        last_seen_gate: str | None = None,
        sync_role: str | None = None,
        allowed_role: str | None = None,
        command_name: str | None = None,
        target_role: str | None = None,
        ping_count: int | None = None,
    ) -> str:
        next_seq = self._next_event_seq(issues)
        metadata = {
            KIND_KEY: "event",
            "milestone": milestone,
            "phase": phase or "",
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
            "last_seen_gate": last_seen_gate or "",
            "sync_role": sync_role or "",
            "allowed_role": allowed_role or "",
            "command_name": command_name or "",
            "target_role": target_role or "",
            "ping_count": ping_count,
            "ts": ts,
        }
        rendered_phase = phase or "na"
        return self.store.create_issue(
            title=f"{event} {role} phase {rendered_phase}",
            issue_type="task",
            description=task,
            labels=self._base_labels("event", milestone, phase=phase or None, role=role),
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
        stale_risk: str | None = None,
    ) -> None:
        updates: dict[str, Any] = {
            "agent_state": state,
            "current_task": task,
            "last_activity": last_activity,
            "action": action,
        }
        if stale_risk is not None:
            updates["stale_risk"] = stale_risk
        agent_issue = self._require_single(issues, "agent", role=role)
        agent_metadata = _merge_dicts(
            agent_issue.metadata,
            updates,
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
        phase: str | None,
        gate_id: str | None,
    ) -> str | None:
        if gate_id:
            gate_issue = self._find_single(issues, "gate", gate_id=gate_id)
            if gate_issue is not None:
                return gate_issue.issue_id
        if phase:
            phase_issue = self._find_single(issues, "phase", phase=phase)
            if phase_issue is not None:
                return phase_issue.issue_id
        milestone_issue = self._find_single(issues, "milestone")
        if milestone_issue is not None:
            return milestone_issue.issue_id
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

    def _state_snapshot(
        self,
        issues: Sequence[IssueRecord],
        *,
        preferred_gate_id: str | None = None,
    ) -> dict[str, str | None]:
        gate_issue = None
        if preferred_gate_id:
            gate_issue = self._find_single(issues, "gate", gate_id=preferred_gate_id)
        if gate_issue is None:
            gate_issue = self._latest_gate(issues)
        phase_issue = self._latest_phase(issues)
        phase = ""
        parent_id = None
        gate_id = ""
        target_commit = ""
        allowed_role = ""
        if gate_issue is not None:
            phase = gate_issue.metadata_str("phase")
            gate_id = gate_issue.metadata_str("gate_id")
            target_commit = gate_issue.metadata_str("target_commit")
            allowed_role = gate_issue.metadata_str("allowed_role")
            parent_id = gate_issue.issue_id
        elif phase_issue is not None:
            phase = phase_issue.metadata_str("phase")
            parent_id = phase_issue.issue_id
        else:
            milestone_issue = self._find_single(issues, "milestone")
            if milestone_issue is not None:
                parent_id = milestone_issue.issue_id
        return {
            "phase": phase,
            "gate_id": gate_id,
            "target_commit": target_commit,
            "allowed_role": allowed_role,
            "parent_id": parent_id,
        }

    @staticmethod
    def _latest_gate(issues: Sequence[IssueRecord]) -> IssueRecord | None:
        gates = sorted(
            (
                issue
                for issue in issues
                if issue.metadata_str(KIND_KEY) == "gate"
            ),
            key=lambda issue: (_phase_sort_key(issue.metadata_str("phase")), issue.issue_id),
        )
        return gates[-1] if gates else None

    @staticmethod
    def _latest_phase(issues: Sequence[IssueRecord]) -> IssueRecord | None:
        phases = sorted(
            (
                issue
                for issue in issues
                if issue.metadata_str(KIND_KEY) == "phase"
            ),
            key=lambda issue: (_phase_sort_key(issue.metadata_str("phase")), issue.issue_id),
        )
        return phases[-1] if phases else None

    def _ensure_gate_close_guards(
        self,
        *,
        issues: Sequence[IssueRecord],
        milestone_issue: IssueRecord,
        gate_issue: IssueRecord,
        phase: str,
        result: str,
        report_commit: str,
        report_path: str,
    ) -> None:
        self._ensure_review_event(
            issues=issues,
            gate_id=gate_issue.metadata_str("gate_id"),
            phase=phase,
            result=result,
            report_commit=report_commit,
            report_path=report_path,
        )
        self._ensure_projection_reconciled(
            milestone=milestone_issue.metadata_str("milestone"),
            run_date=milestone_issue.metadata_str("run_date"),
            expected_events=len(list(self._iter_kind(issues, "event"))),
        )
        self._ensure_report_visible(report_commit=report_commit, report_path=report_path)

    def _ensure_review_event(
        self,
        *,
        issues: Sequence[IssueRecord],
        gate_id: str,
        phase: str,
        result: str,
        report_commit: str,
        report_path: str,
    ) -> None:
        review_event = self._find_matching_event(
            issues,
            event="GATE_REVIEW_COMPLETE",
            gate_id=gate_id,
            phase=phase,
            result=result,
            report_commit=report_commit,
            report_path=report_path,
        )
        if review_event is None:
            raise CoordError(
                "cannot close gate without matching GATE_REVIEW_COMPLETE: "
                f"gate={gate_id} phase={phase}"
            )

    def _ensure_projection_reconciled(
        self,
        *,
        milestone: str,
        run_date: str,
        expected_events: int,
    ) -> None:
        heartbeat_path = self.paths.log_dir(milestone, run_date) / "heartbeat_events.jsonl"
        if not heartbeat_path.exists():
            raise CoordError("cannot close gate before heartbeat_events.jsonl has been rendered")
        logged_events = sum(
            1 for line in heartbeat_path.read_text("utf-8").splitlines() if line.strip()
        )
        if logged_events != expected_events:
            raise CoordError(
                "cannot close gate before event reconciliation: "
                f"received_events={expected_events} logged_events={logged_events}"
            )

    def _ensure_report_visible(self, *, report_commit: str, report_path: str) -> None:
        report_relpath = self._repo_relative_path(report_path)
        self._git_cat_file(f"{report_commit}^{{commit}}")
        self._git_cat_file(f"{report_commit}:{report_relpath}")

    def _repo_relative_path(self, report_path: str) -> str:
        candidate = Path(report_path)
        if candidate.is_absolute():
            try:
                return str(candidate.resolve().relative_to(self.paths.workspace_root))
            except ValueError as exc:
                raise CoordError(f"report_path must be inside workspace: {report_path}") from exc
        return str(candidate)

    def _git_cat_file(self, object_name: str) -> None:
        try:
            subprocess.run(
                ["git", "cat-file", "-e", object_name],
                cwd=self.paths.workspace_root,
                capture_output=True,
                check=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip() or exc.stdout.strip()
            raise CoordError(f"git cat-file -e {object_name} failed: {stderr}") from exc

    def _canonicalize_commit_ref(self, commit_ref: str | None) -> str | None:
        if commit_ref in (None, ""):
            return commit_ref
        try:
            return _git_output(
                self.paths.workspace_root,
                "rev-parse",
                "--verify",
                f"{commit_ref}^{{commit}}",
            )
        except CoordError:
            return commit_ref

    def _find_matching_event(
        self,
        issues: Sequence[IssueRecord],
        *,
        event: str,
        gate_id: str,
        phase: str,
        result: str,
        report_commit: str,
        report_path: str,
    ) -> IssueRecord | None:
        candidates = [
            issue
            for issue in self._iter_kind(issues, "event")
            if issue.metadata_str("event") == event
            and issue.metadata_str("gate") == gate_id
            and issue.metadata_str("phase") == phase
            and issue.metadata_str("result") == result
            and issue.metadata_str("report_commit") == report_commit
            and issue.metadata_str("report_path") == report_path
        ]
        candidates.sort(key=lambda issue: (issue.metadata_int("event_seq"), issue.issue_id))
        return candidates[-1] if candidates else None

    def _find_latest_event(
        self,
        issues: Sequence[IssueRecord],
        *,
        event: str,
        **matches: str,
    ) -> IssueRecord | None:
        candidates = [
            issue
            for issue in self._iter_kind(issues, "event")
            if issue.metadata_str("event") == event
            and all(issue.metadata_str(key) == value for key, value in matches.items())
        ]
        candidates.sort(key=lambda issue: (issue.metadata_int("event_seq"), issue.issue_id))
        return candidates[-1] if candidates else None

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
        last_seen_gate = issue.metadata_str("last_seen_gate")
        if last_seen_gate:
            payload["last_seen_gate"] = last_seen_gate
        sync_role = issue.metadata_str("sync_role")
        if sync_role:
            payload["sync_role"] = _render_role(sync_role)
        allowed_role = issue.metadata_str("allowed_role")
        if allowed_role:
            payload["allowed_role"] = _render_role(allowed_role)
        command_name = issue.metadata_str("command_name")
        if command_name:
            payload["command_name"] = command_name
        target_role = issue.metadata_str("target_role")
        if target_role:
            payload["target_role"] = _render_role(target_role)
        ping_count = issue.metadata.get("ping_count")
        if ping_count is not None:
            payload["ping_count"] = ping_count
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

    def _render_project_progress(
        self,
        *,
        milestone: str,
        run_date: str,
        issues: Sequence[IssueRecord],
        existing: str,
    ) -> str:
        begin_marker = f"<!-- devcoord:begin milestone={milestone} -->"
        end_marker = f"<!-- devcoord:end milestone={milestone} -->"
        block = self._project_progress_block(
            milestone=milestone,
            run_date=run_date,
            issues=issues,
            begin_marker=begin_marker,
            end_marker=end_marker,
        )
        base = existing or "# Project Progress\n"
        pattern = re.compile(
            rf"{re.escape(begin_marker)}\n.*?\n{re.escape(end_marker)}\n?",
            flags=re.DOTALL,
        )
        if pattern.search(base):
            updated = pattern.sub(block, base)
        else:
            updated = base.rstrip() + "\n\n" + block
        return updated if updated.endswith("\n") else updated + "\n"

    def _project_progress_block(
        self,
        *,
        milestone: str,
        run_date: str,
        issues: Sequence[IssueRecord],
        begin_marker: str,
        end_marker: str,
    ) -> str:
        latest_gate = self._latest_gate(issues)
        non_pm_agents = sorted(
            (
                issue
                for issue in self._iter_kind(issues, "agent")
                if issue.metadata_str("role") != "pm"
            ),
            key=lambda issue: issue.metadata_str("role"),
        )
        status = "in_progress"
        done_summary = "control plane initialized"
        evidence_parts = [
            f"`dev_docs/logs/{milestone}_{run_date}/gate_state.md`",
            f"`dev_docs/logs/{milestone}_{run_date}/watchdog_status.md`",
        ]
        next_step = f"等待 {milestone.upper()} 下一条 gate 或 phase 指令"
        risk = "无"
        if latest_gate is not None:
            gate_id = latest_gate.metadata_str("gate_id")
            gate_state = latest_gate.metadata_str("gate_state", "pending")
            gate_result = latest_gate.metadata_str("result")
            agent_summary = ", ".join(
                f"{agent.metadata_str('role')}={agent.metadata_str('agent_state')}"
                for agent in non_pm_agents
            ) or "no-agents"
            done_summary = f"最新 gate {gate_id} 为 {gate_state}"
            if gate_result:
                done_summary += f" ({gate_result})"
            done_summary += f"；{agent_summary}"
            if gate_state == "closed" and gate_result in {"PASS", "PASS_WITH_RISK"}:
                status = "done"
            report_path = latest_gate.metadata_str("report_path")
            report_commit = latest_gate.metadata_str("report_commit")
            if report_path and report_commit:
                evidence_parts.append(f"`{report_path}` ({report_commit})")
            if gate_state == "open":
                next_step = (
                    f"继续推进 {gate_id}，当前 allowed_role="
                    f"{latest_gate.metadata_str('allowed_role') or 'unknown'}"
                )
            elif gate_state == "closed":
                next_step = f"{gate_id} 已关闭，等待 {milestone.upper()} 下一条 gate"
            if gate_result == "FAIL":
                risk = f"{gate_id} 关闭结果为 FAIL"
            elif gate_result == "PASS_WITH_RISK":
                risk = f"{gate_id} 关闭结果为 PASS_WITH_RISK"
        stale_agents = [
            agent.metadata_str("role")
            for agent in non_pm_agents
            if agent.metadata_str("stale_risk", "none") != "none"
        ]
        if stale_agents:
            risk = f"stale_risk={'/'.join(stale_agents)}"
        lines = [
            begin_marker,
            f"## {run_date} (generated) | {milestone.upper()}",
            f"- Status: {status}",
            f"- Done: {done_summary}",
            f"- Evidence: {', '.join(evidence_parts)}",
            f"- Next: {next_step}",
            f"- Risk: {risk}",
            end_marker,
        ]
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



def _normalize_milestone(value: str) -> str:
    return value.strip().lower()


def _normalize_role(value: str) -> str:
    return value.strip().lower()

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
