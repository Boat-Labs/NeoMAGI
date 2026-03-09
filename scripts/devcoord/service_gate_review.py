from __future__ import annotations

from typing import TYPE_CHECKING

from .service_common import _merge_dicts, _normalize_milestone, _normalize_role
from .service_projection import ensure_gate_close_guards
from .service_state import coord_records, record_event, require_single, update_agent

if TYPE_CHECKING:
    from .service import CoordService
    from .store import CoordRecord

__all__ = ["gate_close", "gate_review"]


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
