from __future__ import annotations

from typing import TYPE_CHECKING

from .model import CoordError
from .service_common import _merge_dicts, _normalize_milestone, _normalize_role
from .service_gate_setup import init_control_plane as init_control_plane
from .service_gate_setup import open_gate as open_gate
from .service_projection import ensure_gate_close_guards
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

__all__ = [
    "ack",
    "gate_close",
    "gate_review",
    "init_control_plane",
    "open_gate",
    "phase_complete",
]


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


def phase_complete(
    service: CoordService,
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
    canonical_commit = canonicalize_commit_ref(service, commit)
    with service._locked():
        if _phase_complete_exists(
            service,
            normalized_milestone,
            normalized_role,
            phase,
            gate_id,
            canonical_commit,
        ):
            return
        now = service.now_fn()
        gate_rec, phase_rec = _phase_complete_records(service, normalized_milestone, gate_id, phase)
        _submit_phase(service, gate_rec, phase_rec, canonical_commit)
        _record_phase_complete(
            service,
            normalized_milestone,
            normalized_role,
            phase,
            gate_id,
            task,
            now,
            gate_rec,
            canonical_commit,
            branch,
        )
        _mark_phase_complete_agent(
            service,
            normalized_milestone,
            normalized_role,
            task,
            now,
            gate_id,
        )


def gate_review(
    service: CoordService,
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
    now = service.now_fn()
    with service._locked():
        _apply_gate_review(
            service,
            normalized_milestone,
            phase,
            gate_id,
            normalized_role,
            normalized_result,
            report_commit,
            report_path,
            task,
            now,
        )
        _mark_gate_review_agent(
            service,
            normalized_milestone,
            normalized_role,
            task,
            now,
            gate_id,
        )


def gate_close(
    service: CoordService,
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
    now = service.now_fn()
    with service._locked():
        records, milestone_rec, gate_rec, phase_rec = _gate_close_context(
            service, normalized_milestone, gate_id, phase
        )
        ensure_gate_close_guards(
            service,
            records=records,
            milestone_rec=milestone_rec,
            gate_rec=gate_rec,
            phase=phase,
            result=normalized_result,
            report_commit=report_commit,
            report_path=report_path,
        )
        _close_gate_records(
            service,
            gate_rec,
            phase_rec,
            normalized_result,
            report_commit,
            report_path,
            now,
        )
        _record_gate_close(
            service,
            normalized_milestone,
            phase,
            gate_id,
            normalized_result,
            report_commit,
            report_path,
            task,
            now,
            gate_rec,
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


def _phase_complete_records(
    service: CoordService,
    milestone: str,
    gate_id: str,
    phase: str,
) -> tuple[CoordRecord, CoordRecord]:
    records = coord_records(service, milestone)
    gate_rec = require_single(records, "gate", gate_id=gate_id)
    phase_rec = require_single(records, "phase", phase=phase)
    return (gate_rec, phase_rec)


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


def _record_phase_complete(
    service: CoordService,
    milestone: str,
    role: str,
    phase: str,
    gate_id: str,
    task: str,
    now: str,
    gate_rec: CoordRecord,
    canonical_commit: str | None,
    branch: str | None,
) -> None:
    record_event(
        service,
        records=coord_records(service, milestone),
        milestone=milestone,
        phase=phase,
        role=role,
        status="done",
        task=task,
        event="PHASE_COMPLETE",
        gate_id=gate_id,
        target_commit=canonical_commit,
        parent_id=gate_rec.record_id,
        ts=now,
        eta_min=0,
        branch=branch,
    )


def _mark_phase_complete_agent(
    service: CoordService,
    milestone: str,
    role: str,
    task: str,
    now: str,
    gate_id: str,
) -> None:
    update_agent(
        service,
        coord_records(service, milestone),
        milestone=milestone,
        role=role,
        state="done",
        task=task,
        last_activity=now,
        action=f"waiting for next gate after {gate_id}",
        stale_risk="none",
    )


def _apply_gate_review(
    service: CoordService,
    milestone: str,
    phase: str,
    gate_id: str,
    role: str,
    result: str,
    report_commit: str,
    report_path: str,
    task: str,
    now: str,
) -> None:
    gate_rec = require_single(coord_records(service, milestone), "gate", gate_id=gate_id)
    service.store.update_record(
        gate_rec.record_id,
        metadata=_merge_dicts(
            gate_rec.metadata,
            {
                "result": result,
                "report_commit": report_commit,
                "report_path": report_path,
            },
        ),
    )
    record_event(
        service,
        records=coord_records(service, milestone),
        milestone=milestone,
        phase=phase,
        role=role,
        status="done",
        task=task,
        event="GATE_REVIEW_COMPLETE",
        gate_id=gate_id,
        target_commit=gate_rec.metadata_str("target_commit"),
        parent_id=gate_rec.record_id,
        ts=now,
        eta_min=0,
        result=result,
        report_commit=report_commit,
        report_path=report_path,
    )
def _mark_gate_review_agent(
    service: CoordService,
    milestone: str,
    role: str,
    task: str,
    now: str,
    gate_id: str,
) -> None:
    update_agent(
        service,
        coord_records(service, milestone),
        milestone=milestone,
        role=role,
        state="done",
        task=task,
        last_activity=now,
        action=f"review submitted for {gate_id}",
        stale_risk="none",
    )


def _gate_close_context(
    service: CoordService,
    milestone: str,
    gate_id: str,
    phase: str,
) -> tuple[list[CoordRecord], CoordRecord, CoordRecord, CoordRecord]:
    records = coord_records(service, milestone)
    milestone_rec = require_single(records, "milestone")
    gate_rec = require_single(records, "gate", gate_id=gate_id)
    phase_rec = require_single(records, "phase", phase=phase)
    return (records, milestone_rec, gate_rec, phase_rec)


def _record_gate_close(
    service: CoordService,
    milestone: str,
    phase: str,
    gate_id: str,
    result: str,
    report_commit: str,
    report_path: str,
    task: str,
    now: str,
    gate_rec: CoordRecord,
) -> None:
    record_event(
        service,
        records=coord_records(service, milestone),
        milestone=milestone,
        phase=phase,
        role="pm",
        status="working",
        task=task,
        event="GATE_CLOSE",
        gate_id=gate_id,
        target_commit=gate_rec.metadata_str("target_commit"),
        parent_id=gate_rec.record_id,
        ts=now,
        result=result,
        report_commit=report_commit,
        report_path=report_path,
    )


def _phase_complete_exists(
    service: CoordService,
    milestone: str,
    role: str,
    phase: str,
    gate_id: str,
    canonical_commit: str | None,
) -> bool:
    return (
        find_latest_event(
            coord_records(service, milestone),
            event="PHASE_COMPLETE",
            role=role,
            gate=gate_id,
            phase=phase,
            target_commit=canonical_commit or "",
        )
        is not None
    )


def _submit_phase(
    service: CoordService,
    gate_rec: CoordRecord,
    phase_rec: CoordRecord,
    canonical_commit: str | None,
) -> None:
    service.store.update_record(
        gate_rec.record_id,
        metadata=_merge_dicts(gate_rec.metadata, {"target_commit": canonical_commit}),
    )
    service.store.update_record(
        phase_rec.record_id,
        metadata=_merge_dicts(
            phase_rec.metadata,
            {"phase_state": "submitted", "last_commit": canonical_commit},
        ),
    )


def _close_gate_records(
    service: CoordService,
    gate_rec: CoordRecord,
    phase_rec: CoordRecord,
    result: str,
    report_commit: str,
    report_path: str,
    now: str,
) -> None:
    service.store.update_record(
        gate_rec.record_id,
        metadata=_merge_dicts(
            gate_rec.metadata,
            {
                "result": result,
                "report_commit": report_commit,
                "report_path": report_path,
                "gate_state": "closed",
                "closed_at": now,
            },
        ),
    )
    service.store.update_record(
        phase_rec.record_id,
        metadata=_merge_dicts(phase_rec.metadata, {"phase_state": "closed"}),
    )
