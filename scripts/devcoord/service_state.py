from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from .model import COORD_LABEL, KIND_KEY, CoordError, _git_output
from .service_common import _merge_dicts, _phase_sort_key
from .store import CoordRecord

if TYPE_CHECKING:
    from .service import CoordService

_EVENT_TEXT_FIELDS = (
    "ack_of",
    "result",
    "branch",
    "report_commit",
    "report_path",
    "source_message_id",
    "last_seen_gate",
    "sync_role",
    "allowed_role",
    "command_name",
    "target_role",
)
_EVENT_RAW_FIELDS = ("eta_min", "ping_count")


def coord_records(
    service: CoordService,
    milestone: str,
    *,
    kind: str | None = None,
) -> list[CoordRecord]:
    return service.store.list_records(milestone, kind=kind)


def ensure_phase(
    service: CoordService,
    records: Sequence[CoordRecord],
    milestone_rec: CoordRecord,
    milestone: str,
    phase: str,
) -> CoordRecord:
    phase_rec = find_single(records, "phase", phase=phase)
    metadata = {
        KIND_KEY: "phase",
        "milestone": milestone,
        "phase": phase,
        "phase_state": "in_progress",
        "last_commit": "",
    }
    labels = base_labels("phase", milestone, phase=phase)
    if phase_rec is None:
        return service.store.create_record(
            title=f"Coord phase {phase}",
            record_type="task",
            description=f"Coordination phase {phase} for {milestone}.",
            labels=labels,
            metadata=metadata,
            parent_id=milestone_rec.record_id,
        )
    return service.store.update_record(
        phase_rec.record_id,
        labels=labels,
        metadata=_merge_dicts(phase_rec.metadata, metadata),
    )


def record_event(
    service: CoordService,
    *,
    records: Sequence[CoordRecord],
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
    **extra: Any,
) -> str:
    metadata = _event_metadata(
        milestone=milestone,
        phase=phase,
        role=role,
        status=status,
        task=task,
        event=event,
        gate_id=gate_id,
        target_commit=target_commit,
        event_seq=next_event_seq(records),
        ts=ts,
        **extra,
    )
    rec = service.store.create_record(
        title=_event_title(event, role, phase),
        record_type="task",
        description=task,
        labels=base_labels("event", milestone, phase=phase or None, role=role),
        metadata=metadata,
        parent_id=parent_id,
    )
    return rec.record_id


def update_agent(
    service: CoordService,
    records: Sequence[CoordRecord],
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
    agent_rec = require_single(records, "agent", role=role)
    service.store.update_record(
        agent_rec.record_id,
        metadata=_merge_dicts(agent_rec.metadata, updates),
        labels=base_labels("agent", milestone, role=role),
    )


def event_parent_id(
    records: Sequence[CoordRecord],
    *,
    phase: str | None,
    gate_id: str | None,
) -> str | None:
    if gate_id:
        gate_rec = find_single(records, "gate", gate_id=gate_id)
        if gate_rec is not None:
            return gate_rec.record_id
    if phase:
        phase_rec = find_single(records, "phase", phase=phase)
        if phase_rec is not None:
            return phase_rec.record_id
    milestone_rec = find_single(records, "milestone")
    return milestone_rec.record_id if milestone_rec is not None else None


def find_pending_message(
    records: Sequence[CoordRecord],
    *,
    role: str,
    gate_id: str,
    command: str,
) -> CoordRecord | None:
    candidates = [
        rec
        for rec in iter_kind(records, "message")
        if rec.metadata_str("role") == role
        and rec.metadata_str("gate_id") == gate_id
        and rec.metadata_str("command").upper() == command
        and not rec.metadata_bool("effective")
    ]
    candidates.sort(key=lambda rec: rec.record_id)
    return candidates[-1] if candidates else None


def create_message(
    service: CoordService,
    *,
    milestone: str,
    phase: str,
    gate_id: str,
    role: str,
    command: str,
    target_commit: str | None,
    allowed_role: str,
    sent_at: str,
    task: str,
    parent_id: str,
) -> CoordRecord:
    metadata = {
        KIND_KEY: "message",
        "milestone": milestone,
        "phase": phase,
        "gate_id": gate_id,
        "role": role,
        "command": command,
        "requires_ack": True,
        "effective": False,
        "target_commit": target_commit,
        "allowed_role": allowed_role,
        "sent_at": sent_at,
        "task": task,
    }
    return service.store.create_record(
        title=f"{command} -> {role}",
        record_type="task",
        description=task,
        labels=base_labels("message", milestone, phase=phase, role=role),
        metadata=metadata,
        assignee=role,
        parent_id=parent_id,
    )


def base_labels(
    kind: str,
    milestone: str,
    *,
    phase: str | None = None,
    role: str | None = None,
) -> list[str]:
    labels = [COORD_LABEL, f"coord-kind-{kind}", f"coord-milestone-{milestone}"]
    if phase is not None:
        labels.append(f"coord-phase-{phase}")
    if role is not None:
        labels.append(f"coord-role-{role}")
    return labels


def find_single(
    records: Sequence[CoordRecord],
    kind: str,
    **matches: str,
) -> CoordRecord | None:
    candidates = [
        rec
        for rec in records
        if rec.metadata_str(KIND_KEY) == kind
        and all(rec.metadata_str(key) == value for key, value in matches.items())
    ]
    candidates.sort(key=lambda rec: rec.record_id)
    return candidates[-1] if candidates else None


def require_single(
    records: Sequence[CoordRecord],
    kind: str,
    **matches: str,
) -> CoordRecord:
    rec = find_single(records, kind, **matches)
    if rec is not None:
        return rec
    filters = ", ".join(f"{key}={value}" for key, value in matches.items())
    raise CoordError(f"missing {kind} record for {filters or 'control plane'}")


def iter_kind(records: Sequence[CoordRecord], kind: str) -> Iterable[CoordRecord]:
    return (rec for rec in records if rec.metadata_str(KIND_KEY) == kind)


def state_snapshot(
    records: Sequence[CoordRecord],
    *,
    preferred_gate_id: str | None = None,
) -> dict[str, str | None]:
    gate_rec = _preferred_gate(records, preferred_gate_id)
    phase_rec = latest_phase(records)
    if gate_rec is not None:
        return _gate_snapshot(gate_rec)
    if phase_rec is not None:
        return _phase_snapshot(phase_rec)
    return _milestone_snapshot(records)


def latest_gate(records: Sequence[CoordRecord]) -> CoordRecord | None:
    gates = sorted(
        (rec for rec in records if rec.metadata_str(KIND_KEY) == "gate"),
        key=lambda rec: (_phase_sort_key(rec.metadata_str("phase")), rec.record_id),
    )
    return gates[-1] if gates else None


def latest_phase(records: Sequence[CoordRecord]) -> CoordRecord | None:
    phases = sorted(
        (rec for rec in records if rec.metadata_str(KIND_KEY) == "phase"),
        key=lambda rec: (_phase_sort_key(rec.metadata_str("phase")), rec.record_id),
    )
    return phases[-1] if phases else None


def canonicalize_commit_ref(service: CoordService, commit_ref: str | None) -> str | None:
    if commit_ref in (None, ""):
        return commit_ref
    try:
        return _git_output(
            service.paths.workspace_root,
            "rev-parse",
            "--verify",
            f"{commit_ref}^{{commit}}",
        )
    except CoordError:
        return commit_ref


def find_matching_event(
    records: Sequence[CoordRecord],
    *,
    event: str,
    gate_id: str,
    phase: str,
    result: str,
    report_commit: str,
    report_path: str,
) -> CoordRecord | None:
    candidates = [
        rec
        for rec in iter_kind(records, "event")
        if rec.metadata_str("event") == event
        and rec.metadata_str("gate") == gate_id
        and rec.metadata_str("phase") == phase
        and rec.metadata_str("result") == result
        and rec.metadata_str("report_commit") == report_commit
        and rec.metadata_str("report_path") == report_path
    ]
    candidates.sort(key=lambda rec: (rec.metadata_int("event_seq"), rec.record_id))
    return candidates[-1] if candidates else None


def find_latest_event(
    records: Sequence[CoordRecord],
    *,
    event: str,
    **matches: str,
) -> CoordRecord | None:
    candidates = [
        rec
        for rec in iter_kind(records, "event")
        if rec.metadata_str("event") == event
        and all(rec.metadata_str(key) == value for key, value in matches.items())
    ]
    candidates.sort(key=lambda rec: (rec.metadata_int("event_seq"), rec.record_id))
    return candidates[-1] if candidates else None


def next_event_seq(records: Sequence[CoordRecord]) -> int:
    max_seq = 0
    for rec in iter_kind(records, "event"):
        max_seq = max(max_seq, rec.metadata_int("event_seq"))
    return max_seq + 1


def _event_metadata(
    *,
    milestone: str,
    phase: str,
    role: str,
    status: str,
    task: str,
    event: str,
    gate_id: str | None,
    target_commit: str | None,
    event_seq: int,
    ts: str,
    **extra: Any,
) -> dict[str, Any]:
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
        "event_seq": event_seq,
        "ts": ts,
    }
    metadata.update(_event_optional_metadata(extra))
    return metadata


def _event_optional_metadata(extra: dict[str, Any]) -> dict[str, Any]:
    allowed = set(_EVENT_TEXT_FIELDS) | set(_EVENT_RAW_FIELDS)
    unexpected = sorted(set(extra) - allowed)
    if unexpected:
        fields = ", ".join(unexpected)
        raise CoordError(f"unexpected event metadata fields: {fields}")
    metadata = {field: extra.get(field) or "" for field in _EVENT_TEXT_FIELDS}
    for field in _EVENT_RAW_FIELDS:
        metadata[field] = extra.get(field)
    return metadata


def _event_title(event: str, role: str, phase: str) -> str:
    rendered_phase = phase or "na"
    return f"{event} {role} phase {rendered_phase}"


def _preferred_gate(
    records: Sequence[CoordRecord],
    preferred_gate_id: str | None,
) -> CoordRecord | None:
    if preferred_gate_id:
        gate_rec = find_single(records, "gate", gate_id=preferred_gate_id)
        if gate_rec is not None:
            return gate_rec
    return latest_gate(records)


def _gate_snapshot(gate_rec: CoordRecord) -> dict[str, str | None]:
    return {
        "phase": gate_rec.metadata_str("phase"),
        "gate_id": gate_rec.metadata_str("gate_id"),
        "target_commit": gate_rec.metadata_str("target_commit"),
        "allowed_role": gate_rec.metadata_str("allowed_role"),
        "parent_id": gate_rec.record_id,
    }


def _phase_snapshot(phase_rec: CoordRecord) -> dict[str, str | None]:
    return {
        "phase": phase_rec.metadata_str("phase"),
        "gate_id": "",
        "target_commit": "",
        "allowed_role": "",
        "parent_id": phase_rec.record_id,
    }


def _milestone_snapshot(records: Sequence[CoordRecord]) -> dict[str, str | None]:
    milestone_rec = find_single(records, "milestone")
    return {
        "phase": "",
        "gate_id": "",
        "target_commit": "",
        "allowed_role": "",
        "parent_id": milestone_rec.record_id if milestone_rec is not None else None,
    }
