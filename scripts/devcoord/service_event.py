from __future__ import annotations

from typing import TYPE_CHECKING

from .model import CoordError
from .service_common import (
    _none_if_placeholder,
    _normalize_milestone,
    _normalize_role,
    _to_agent_state,
)
from .service_state import (
    canonicalize_commit_ref,
    coord_records,
    create_message,
    event_parent_id,
    record_event,
    require_single,
    state_snapshot,
    update_agent,
)

if TYPE_CHECKING:
    from .service import CoordService


def heartbeat(
    service: CoordService,
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
    canonical_target_commit = canonicalize_commit_ref(service, target_commit)
    now = service.now_fn()
    with service._locked():
        records = coord_records(service, normalized_milestone)
        record_event(
            service,
            records=records,
            milestone=normalized_milestone,
            phase=phase,
            role=normalized_role,
            status=status,
            task=task,
            event="HEARTBEAT",
            gate_id=gate_id,
            target_commit=canonical_target_commit,
            parent_id=event_parent_id(records, phase=phase, gate_id=gate_id),
            ts=now,
            eta_min=eta_min,
            branch=branch,
        )
        update_agent(
            service,
            coord_records(service, normalized_milestone),
            milestone=normalized_milestone,
            role=normalized_role,
            state=_to_agent_state(status),
            task=task,
            last_activity=now,
            action="reporting progress",
            stale_risk="none",
        )


def recovery_check(
    service: CoordService,
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
    with service._locked():
        records = coord_records(service, normalized_milestone)
        snapshot = state_snapshot(records, preferred_gate_id=requested_gate)
        duplicate = _duplicate_recovery(
            records,
            normalized_role,
            snapshot,
            normalized_last_seen_gate,
        )
        if duplicate is not None:
            return
        now = service.now_fn()
        record_event(
            service,
            records=records,
            milestone=normalized_milestone,
            phase=snapshot["phase"] or "",
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
        update_agent(
            service,
            coord_records(service, normalized_milestone),
            milestone=normalized_milestone,
            role=normalized_role,
            state="stuck",
            task=task,
            last_activity=now,
            action=_recovery_action(snapshot["gate_id"]),
        )


def state_sync_ok(
    service: CoordService,
    milestone: str,
    *,
    role: str,
    gate_id: str,
    target_commit: str,
    task: str,
) -> None:
    normalized_milestone = _normalize_milestone(milestone)
    normalized_role = _normalize_role(role)
    canonical_target_commit = canonicalize_commit_ref(service, target_commit)
    now = service.now_fn()
    with service._locked():
        records = coord_records(service, normalized_milestone)
        gate_rec = require_single(records, "gate", gate_id=gate_id)
        resolved_target_commit = _resolved_state_sync_commit(
            gate_rec,
            canonical_target_commit,
            gate_id,
        )
        phase = gate_rec.metadata_str("phase")
        allowed_role = gate_rec.metadata_str("allowed_role")
        record_event(
            service,
            records=records,
            milestone=normalized_milestone,
            phase=phase,
            role="pm",
            status="working",
            task=task,
            event="STATE_SYNC_OK",
            gate_id=gate_id,
            target_commit=resolved_target_commit,
            parent_id=gate_rec.record_id,
            ts=now,
            sync_role=normalized_role,
            allowed_role=allowed_role,
        )
        update_agent(
            service,
            coord_records(service, normalized_milestone),
            milestone=normalized_milestone,
            role=normalized_role,
            state="idle",
            task=task,
            last_activity=now,
            action=_state_sync_action(gate_id, resolved_target_commit),
            stale_risk="none",
        )


def stale_detected(
    service: CoordService,
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
    canonical_target_commit = canonicalize_commit_ref(service, target_commit)
    now = service.now_fn()
    with service._locked():
        records = coord_records(service, normalized_milestone)
        record_event(
            service,
            records=records,
            milestone=normalized_milestone,
            phase=phase,
            role=normalized_role,
            status="stuck",
            task=task,
            event="STALE_DETECTED",
            gate_id=gate_id,
            target_commit=canonical_target_commit,
            parent_id=event_parent_id(records, phase=phase, gate_id=gate_id),
            ts=now,
            ping_count=ping_count,
        )
        update_agent(
            service,
            coord_records(service, normalized_milestone),
            milestone=normalized_milestone,
            role=normalized_role,
            state="stuck",
            task=task,
            last_activity=now,
            action=_stale_action(gate_id),
            stale_risk="suspected_stale",
        )


def ping(
    service: CoordService,
    milestone: str,
    *,
    role: str,
    phase: str,
    gate_id: str,
    task: str,
    target_commit: str | None = None,
    command_name: str = "PING",
) -> None:
    normalized_milestone = _normalize_milestone(milestone)
    normalized_role = _normalize_role(role)
    canonical_target_commit = canonicalize_commit_ref(service, target_commit)
    command = command_name.upper()
    now = service.now_fn()
    with service._locked():
        records = coord_records(service, normalized_milestone)
        gate_rec = require_single(records, "gate", gate_id=gate_id)
        resolved_target_commit = canonical_target_commit or gate_rec.metadata_str("target_commit")
        create_message(
            service,
            milestone=normalized_milestone,
            phase=phase,
            gate_id=gate_id,
            role=normalized_role,
            command=command,
            target_commit=resolved_target_commit,
            allowed_role=normalized_role,
            sent_at=now,
            task=task,
            parent_id=gate_rec.record_id,
        )
        record_event(
            service,
            records=coord_records(service, normalized_milestone),
            milestone=normalized_milestone,
            phase=phase,
            role="pm",
            status="working",
            task=task,
            event=f"{command}_SENT",
            gate_id=gate_id,
            target_commit=resolved_target_commit,
            parent_id=gate_rec.record_id,
            ts=now,
            target_role=normalized_role,
        )


def unconfirmed_instruction(
    service: CoordService,
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
    canonical_target_commit = canonicalize_commit_ref(service, target_commit)
    now = service.now_fn()
    with service._locked():
        records = coord_records(service, normalized_milestone)
        gate_rec = require_single(records, "gate", gate_id=gate_id)
        resolved_target_commit = canonical_target_commit or gate_rec.metadata_str("target_commit")
        record_event(
            service,
            records=records,
            milestone=normalized_milestone,
            phase=phase,
            role="pm",
            status="working",
            task=task,
            event="UNCONFIRMED_INSTRUCTION",
            gate_id=gate_id,
            target_commit=resolved_target_commit,
            parent_id=gate_rec.record_id,
            ts=now,
            command_name=command.upper(),
            target_role=normalized_role,
            ping_count=ping_count,
        )


def log_pending(
    service: CoordService,
    milestone: str,
    *,
    phase: str,
    task: str,
    gate_id: str | None = None,
    target_commit: str | None = None,
) -> None:
    normalized_milestone = _normalize_milestone(milestone)
    canonical_target_commit = canonicalize_commit_ref(service, target_commit)
    now = service.now_fn()
    with service._locked():
        records = coord_records(service, normalized_milestone)
        record_event(
            service,
            records=records,
            milestone=normalized_milestone,
            phase=phase,
            role="pm",
            status="blocked",
            task=task,
            event="LOG_PENDING",
            gate_id=gate_id,
            target_commit=canonical_target_commit,
            parent_id=event_parent_id(records, phase=phase, gate_id=gate_id),
            ts=now,
        )


def _duplicate_recovery(
    records,
    role: str,
    snapshot: dict[str, str | None],
    last_seen_gate: str,
):
    from .service_state import find_latest_event

    return find_latest_event(
        records,
        event="RECOVERY_CHECK",
        role=role,
        gate=snapshot["gate_id"] or "",
        phase=snapshot["phase"] or "",
        target_commit=snapshot["target_commit"] or "",
        last_seen_gate=last_seen_gate,
    )


def _recovery_action(gate_id: str | None) -> str:
    if gate_id:
        return f"awaiting state sync for {gate_id}"
    return "awaiting state sync from PM"


def _resolved_state_sync_commit(
    gate_rec,
    canonical_target_commit: str | None,
    gate_id: str,
) -> str | None:
    current_target_commit = gate_rec.metadata_str("target_commit")
    if current_target_commit and canonical_target_commit != current_target_commit:
        raise CoordError(
            "state sync target_commit mismatch: "
            f"gate={gate_id} expected={current_target_commit} got={canonical_target_commit}"
        )
    return current_target_commit or canonical_target_commit


def _state_sync_action(gate_id: str, target_commit: str | None) -> str:
    suffix = f" ({target_commit})" if target_commit else ""
    return f"resume at {gate_id}{suffix}"


def _stale_action(gate_id: str | None) -> str:
    if gate_id:
        return f"stale detected on {gate_id}; investigate and recover"
    return "stale detected; investigate and recover"
