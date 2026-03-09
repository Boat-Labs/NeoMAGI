from __future__ import annotations

from typing import TYPE_CHECKING

from .service_common import _merge_dicts, _normalize_milestone, _normalize_role
from .service_state import (
    canonicalize_commit_ref,
    coord_records,
    find_latest_event,
    record_event,
    require_single,
    update_agent,
)

if TYPE_CHECKING:
    from .service import CoordService
    from .store import CoordRecord

__all__ = ["phase_complete"]


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
