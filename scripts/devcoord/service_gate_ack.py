from __future__ import annotations

from typing import TYPE_CHECKING

from .model import CoordError
from .service_common import _merge_dicts, _normalize_milestone, _normalize_role
from .service_state import (
    canonicalize_commit_ref,
    coord_records,
    find_latest_event,
    find_pending_message,
    record_event,
    require_single,
    update_agent,
)

if TYPE_CHECKING:
    from .service import CoordService
    from .store import CoordRecord

__all__ = ["ack"]


def ack(
    service: CoordService,
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
    canonical_commit = canonicalize_commit_ref(service, commit)
    with service._locked():
        gate_rec, resolved_phase, duplicate_ack, message_rec = _ack_context(
            service,
            normalized_milestone,
            normalized_role,
            command_name,
            gate_id,
            canonical_commit,
            phase,
        )
        now = service.now_fn()
        _mark_message_effective(service, message_rec, normalized_role, canonical_commit, now)
        _finish_ack(
            service,
            normalized_milestone,
            normalized_role,
            command_name,
            gate_id,
            canonical_commit,
            resolved_phase,
            task,
            now,
            gate_rec,
            duplicate_ack,
            message_rec,
        )


def _ack_context(
    service: CoordService,
    milestone: str,
    role: str,
    command_name: str,
    gate_id: str,
    canonical_commit: str | None,
    phase: str | None,
) -> tuple[CoordRecord, str, CoordRecord | None, CoordRecord]:
    records = coord_records(service, milestone)
    gate_rec = require_single(records, "gate", gate_id=gate_id)
    resolved_phase = phase or gate_rec.metadata_str("phase")
    duplicate_ack = find_latest_event(
        records,
        event="ACK",
        role=role,
        gate=gate_id,
        phase=resolved_phase,
        ack_of=command_name,
        target_commit=canonical_commit or "",
    )
    message_rec = find_pending_message(records, role=role, gate_id=gate_id, command=command_name)
    if message_rec is None:
        raise CoordError(f"no pending {command_name} message for role={role} gate={gate_id}")
    return (gate_rec, resolved_phase, duplicate_ack, message_rec)


def _mark_message_effective(
    service: CoordService,
    message_rec: CoordRecord,
    role: str,
    canonical_commit: str | None,
    now: str,
) -> None:
    service.store.update_record(
        message_rec.record_id,
        metadata=_merge_dicts(
            message_rec.metadata,
            {
                "effective": True,
                "acked_at": now,
                "ack_role": role,
                "ack_commit": canonical_commit,
            },
        ),
    )


def _finish_ack(
    service: CoordService,
    milestone: str,
    role: str,
    command_name: str,
    gate_id: str,
    canonical_commit: str | None,
    phase: str,
    task: str,
    now: str,
    gate_rec: CoordRecord,
    duplicate_ack: CoordRecord | None,
    message_rec: CoordRecord,
) -> None:
    if duplicate_ack is not None:
        _apply_duplicate_ack(
            service,
            milestone,
            role,
            gate_id,
            canonical_commit,
            task,
            now,
            gate_rec,
            duplicate_ack,
        )
        return
    _record_fresh_ack(
        service,
        milestone,
        role,
        command_name,
        gate_id,
        canonical_commit,
        phase,
        task,
        now,
        gate_rec,
        message_rec.record_id,
    )


def _apply_duplicate_ack(
    service: CoordService,
    milestone: str,
    role: str,
    gate_id: str,
    canonical_commit: str | None,
    task: str,
    now: str,
    gate_rec: CoordRecord,
    duplicate_ack: CoordRecord,
) -> None:
    service.store.update_record(
        gate_rec.record_id,
        metadata=_merge_dicts(
            gate_rec.metadata,
            {
                "gate_state": "open",
                "opened_at": gate_rec.metadata_str("opened_at") or duplicate_ack.metadata_str("ts"),
                "target_commit": canonical_commit,
            },
        ),
    )
    update_agent(
        service,
        coord_records(service, milestone),
        milestone=milestone,
        role=role,
        state="working",
        task=duplicate_ack.metadata_str("task") or task,
        last_activity=now,
        action=f"gate {gate_id} effective",
        stale_risk="none",
    )


def _record_fresh_ack(
    service: CoordService,
    milestone: str,
    role: str,
    command_name: str,
    gate_id: str,
    canonical_commit: str | None,
    phase: str,
    task: str,
    now: str,
    gate_rec: CoordRecord,
    source_message_id: str,
) -> None:
    _record_ack_event(
        service,
        milestone,
        role,
        phase,
        gate_id,
        command_name,
        canonical_commit,
        task,
        now,
        gate_rec.record_id,
        source_message_id,
    )
    _open_gate_from_ack(service, gate_rec, canonical_commit, now)
    _record_gate_effective_event(
        service,
        milestone,
        role,
        phase,
        gate_id,
        command_name,
        canonical_commit,
        now,
        gate_rec.record_id,
        source_message_id,
    )
    _mark_ack_agent_working(service, milestone, role, gate_id, task, now)


def _record_ack_event(
    service: CoordService,
    milestone: str,
    role: str,
    phase: str,
    gate_id: str,
    command_name: str,
    canonical_commit: str | None,
    task: str,
    now: str,
    gate_record_id: str,
    source_message_id: str,
) -> None:
    record_event(
        service,
        records=coord_records(service, milestone),
        milestone=milestone,
        phase=phase,
        role=role,
        status="working",
        task=task,
        event="ACK",
        gate_id=gate_id,
        target_commit=canonical_commit,
        parent_id=gate_record_id,
        ts=now,
        ack_of=command_name,
        source_message_id=source_message_id,
    )


def _open_gate_from_ack(
    service: CoordService,
    gate_rec: CoordRecord,
    canonical_commit: str | None,
    now: str,
) -> None:
    service.store.update_record(
        gate_rec.record_id,
        metadata=_merge_dicts(
            gate_rec.metadata,
            {
                "gate_state": "open",
                "opened_at": now,
                "target_commit": canonical_commit,
            },
        ),
    )


def _record_gate_effective_event(
    service: CoordService,
    milestone: str,
    role: str,
    phase: str,
    gate_id: str,
    command_name: str,
    canonical_commit: str | None,
    now: str,
    gate_record_id: str,
    source_message_id: str,
) -> None:
    record_event(
        service,
        records=coord_records(service, milestone),
        milestone=milestone,
        phase=phase,
        role="pm",
        status="working",
        task=f"{command_name} effective for {role}",
        event="GATE_EFFECTIVE",
        gate_id=gate_id,
        target_commit=canonical_commit,
        parent_id=gate_record_id,
        ts=now,
        source_message_id=source_message_id,
    )


def _mark_ack_agent_working(
    service: CoordService,
    milestone: str,
    role: str,
    gate_id: str,
    task: str,
    now: str,
) -> None:
    update_agent(
        service,
        coord_records(service, milestone),
        milestone=milestone,
        role=role,
        state="working",
        task=task,
        last_activity=now,
        action=f"gate {gate_id} effective",
        stale_risk="none",
    )
