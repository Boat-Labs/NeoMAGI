from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from .model import KIND_KEY, SCHEMA_VERSION
from .service_common import _merge_dicts, _normalize_milestone, _normalize_role
from .service_state import (
    base_labels,
    canonicalize_commit_ref,
    coord_records,
    create_message,
    ensure_phase,
    find_single,
    record_event,
    require_single,
    update_agent,
)

if TYPE_CHECKING:
    from .service import CoordService
    from .store import CoordRecord


def init_control_plane(
    service: CoordService,
    milestone: str,
    *,
    run_date: str,
    roles: Sequence[str],
) -> None:
    normalized_milestone = _normalize_milestone(milestone)
    normalized_roles = tuple(_normalize_role(role) for role in roles)
    with service._locked():
        service.store.init_store()
        milestone_rec = _upsert_milestone(service, normalized_milestone, run_date)
        _sync_agents(service, normalized_milestone, milestone_rec, normalized_roles)


def open_gate(
    service: CoordService,
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
    canonical_target_commit = canonicalize_commit_ref(service, target_commit)
    now = service.now_fn()
    with service._locked():
        service.store.init_store()
        records = coord_records(service, normalized_milestone)
        milestone_rec = require_single(records, "milestone")
        phase_rec = ensure_phase(service, records, milestone_rec, normalized_milestone, phase)
        gate_record_id = _upsert_gate(
            service,
            normalized_milestone,
            phase,
            gate_id,
            normalized_role,
            canonical_target_commit,
            phase_rec.record_id,
        )
        _create_gate_open_message(
            service,
            normalized_milestone,
            phase,
            gate_id,
            normalized_role,
            canonical_target_commit,
            task,
            now,
            gate_record_id,
        )
        _record_gate_open_event(
            service,
            normalized_milestone,
            phase,
            gate_id,
            canonical_target_commit,
            task,
            now,
            gate_record_id,
        )
        _mark_agent_spawning(service, normalized_milestone, normalized_role, task, now, gate_id)


def _upsert_milestone(service: CoordService, milestone: str, run_date: str) -> CoordRecord:
    records = coord_records(service, milestone)
    milestone_rec = find_single(records, "milestone")
    metadata = {
        KIND_KEY: "milestone",
        "milestone": milestone,
        "run_date": run_date,
        "schema_version": SCHEMA_VERSION,
    }
    labels = base_labels("milestone", milestone)
    if milestone_rec is None:
        service.store.create_record(
            title=f"Coord milestone {milestone}",
            record_type="epic",
            description=f"NeoMAGI devcoord control plane for {milestone}.",
            labels=labels,
            metadata=metadata,
        )
    else:
        service.store.update_record(
            milestone_rec.record_id,
            labels=labels,
            metadata=_merge_dicts(milestone_rec.metadata, metadata),
        )
    return require_single(coord_records(service, milestone), "milestone")


def _sync_agents(
    service: CoordService,
    milestone: str,
    milestone_rec: CoordRecord,
    roles: Sequence[str],
) -> None:
    records = coord_records(service, milestone)
    for role in roles:
        _upsert_agent(service, milestone, milestone_rec.record_id, role, records)


def _upsert_agent(
    service: CoordService,
    milestone: str,
    milestone_record_id: str,
    role: str,
    records: Sequence[CoordRecord],
) -> None:
    agent_rec = find_single(records, "agent", role=role)
    metadata = {
        KIND_KEY: "agent",
        "milestone": milestone,
        "role": role,
        "agent_state": "idle",
        "last_activity": "",
        "current_task": "",
        "stale_risk": "none",
        "action": "awaiting gate",
    }
    labels = base_labels("agent", milestone, role=role)
    if agent_rec is None:
        service.store.create_record(
            title=f"Agent {role}",
            record_type="task",
            description=f"Coordination state for {role}.",
            labels=labels,
            metadata=metadata,
            parent_id=milestone_record_id,
        )
        return
    service.store.update_record(
        agent_rec.record_id,
        labels=labels,
        metadata=_merge_dicts(agent_rec.metadata, metadata),
    )


def _upsert_gate(
    service: CoordService,
    milestone: str,
    phase: str,
    gate_id: str,
    allowed_role: str,
    target_commit: str | None,
    phase_record_id: str,
) -> str:
    records = coord_records(service, milestone)
    gate_rec = find_single(records, "gate", gate_id=gate_id)
    metadata = {
        KIND_KEY: "gate",
        "milestone": milestone,
        "phase": phase,
        "gate_id": gate_id,
        "allowed_role": allowed_role,
        "target_commit": target_commit,
        "result": "",
        "report_path": "",
        "report_commit": "",
        "gate_state": "pending",
        "opened_at": "",
        "closed_at": "",
    }
    labels = base_labels("gate", milestone, phase=phase, role=allowed_role)
    if gate_rec is None:
        return service.store.create_record(
            title=f"Gate {gate_id}",
            record_type="task",
            description=f"Gate {gate_id} for phase {phase}.",
            labels=labels,
            metadata=metadata,
            parent_id=phase_record_id,
        ).record_id
    service.store.update_record(
        gate_rec.record_id,
        labels=labels,
        metadata=_merge_dicts(gate_rec.metadata, metadata),
    )
    return gate_rec.record_id


def _create_gate_open_message(
    service: CoordService,
    milestone: str,
    phase: str,
    gate_id: str,
    role: str,
    target_commit: str | None,
    task: str,
    now: str,
    gate_record_id: str,
) -> None:
    create_message(
        service,
        milestone=milestone,
        phase=phase,
        gate_id=gate_id,
        role=role,
        command="GATE_OPEN",
        target_commit=target_commit,
        allowed_role=role,
        sent_at=now,
        task=task,
        parent_id=gate_record_id,
    )


def _record_gate_open_event(
    service: CoordService,
    milestone: str,
    phase: str,
    gate_id: str,
    target_commit: str | None,
    task: str,
    now: str,
    gate_record_id: str,
) -> None:
    record_event(
        service,
        records=coord_records(service, milestone),
        milestone=milestone,
        phase=phase,
        role="pm",
        status="working",
        task=task,
        event="GATE_OPEN_SENT",
        gate_id=gate_id,
        target_commit=target_commit,
        parent_id=gate_record_id,
        ts=now,
    )


def _mark_agent_spawning(
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
        state="spawning",
        task=task,
        last_activity=now,
        action=f"awaiting ACK for {gate_id}",
    )
