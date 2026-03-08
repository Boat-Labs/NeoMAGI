from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .model import CoordError
from .service_common import (
    _normalize_milestone,
    _phase_sort_key,
    _render_role,
)
from .service_state import (
    coord_records,
    find_matching_event,
    iter_kind,
    latest_gate,
    require_single,
)
from .store import CoordRecord

if TYPE_CHECKING:
    from .service import CoordService


def audit(service: CoordService, milestone: str) -> dict[str, Any]:
    normalized_milestone = _normalize_milestone(milestone)
    milestone_rec = require_single(
        coord_records(service, normalized_milestone, kind="milestone"),
        "milestone",
    )
    return _audit_snapshot(service, normalized_milestone, milestone_rec)


def render(service: CoordService, milestone: str) -> None:
    normalized_milestone = _normalize_milestone(milestone)
    milestone_rec = require_single(
        coord_records(service, normalized_milestone, kind="milestone"),
        "milestone",
    )
    run_date = _require_run_date(milestone_rec, normalized_milestone)
    log_dir = service.paths.log_dir(normalized_milestone, run_date)
    log_dir.mkdir(parents=True, exist_ok=True)
    events = _sorted_events(service, normalized_milestone)
    _write_heartbeat_projection(log_dir / "heartbeat_events.jsonl", events)
    gate_records = coord_records(service, normalized_milestone, kind="gate")
    gate_state = render_gate_state(gate_records, normalized_milestone)
    (log_dir / "gate_state.md").write_text(gate_state, "utf-8")
    agent_records = coord_records(service, normalized_milestone, kind="agent")
    watchdog_state = render_watchdog(agent_records, normalized_milestone)
    (log_dir / "watchdog_status.md").write_text(watchdog_state, "utf-8")
    _write_project_progress(service, normalized_milestone, run_date, gate_records, agent_records)


def close_milestone(service: CoordService, milestone: str) -> None:
    normalized_milestone = _normalize_milestone(milestone)
    with service._locked():
        records = coord_records(service, normalized_milestone)
        milestone_rec = require_single(records, "milestone")
        snapshot = _audit_snapshot(service, normalized_milestone, milestone_rec)
        _ensure_milestone_can_close(snapshot)
        _close_records(service, records)


def ensure_gate_close_guards(
    service: CoordService,
    *,
    records: list[CoordRecord],
    milestone_rec: CoordRecord,
    gate_rec: CoordRecord,
    phase: str,
    result: str,
    report_commit: str,
    report_path: str,
) -> None:
    _ensure_review_event(
        records=records,
        gate_id=gate_rec.metadata_str("gate_id"),
        phase=phase,
        result=result,
        report_commit=report_commit,
        report_path=report_path,
    )
    _ensure_projection_reconciled(
        service,
        milestone=milestone_rec.metadata_str("milestone"),
        run_date=milestone_rec.metadata_str("run_date"),
        expected_events=len(list(iter_kind(records, "event"))),
    )
    _ensure_report_visible(service, report_commit=report_commit, report_path=report_path)


def event_projection(rec: CoordRecord) -> dict[str, Any]:
    payload = {
        "ts": rec.metadata_str("ts"),
        "role": _render_role(rec.metadata_str("role")),
        "phase": rec.metadata_str("phase"),
        "status": rec.metadata_str("status"),
        "task": rec.metadata_str("task"),
        "event": rec.metadata_str("event"),
        "gate": rec.metadata_str("gate"),
        "target_commit": rec.metadata_str("target_commit"),
        "event_seq": rec.metadata_int("event_seq"),
        "eta_min": rec.metadata.get("eta_min"),
    }
    for source, target, render_role in _OPTIONAL_EVENT_FIELDS:
        value = rec.metadata_str(source)
        if value:
            payload[target] = _render_role(value) if render_role else value
    ping_count = rec.metadata.get("ping_count")
    if ping_count is not None:
        payload["ping_count"] = ping_count
    return payload


def render_gate_state(gate_records: list[CoordRecord], milestone: str) -> str:
    lines = [
        f"# {milestone.upper()} Gate State",
        "",
        "| Gate | Phase | Status | Result | Opened | Closed | Target Commit | Report |",
        "|------|-------|--------|--------|--------|--------|---------------|--------|",
    ]
    lines.extend(_gate_state_rows(gate_records))
    return "\n".join(lines) + "\n"


def render_watchdog(agent_records: list[CoordRecord], milestone: str) -> str:
    lines = [
        f"# {milestone.upper()} Watchdog Status",
        "",
        "| role | status | last_heartbeat | current_task | stale_risk | action |",
        "|------|--------|----------------|--------------|------------|--------|",
    ]
    lines.extend(_watchdog_rows(agent_records))
    return "\n".join(lines) + "\n"


def render_project_progress(
    service: CoordService,
    *,
    milestone: str,
    run_date: str,
    gate_records: list[CoordRecord],
    agent_records: list[CoordRecord],
    existing: str,
) -> str:
    begin_marker = f"<!-- devcoord:begin milestone={milestone} -->"
    end_marker = f"<!-- devcoord:end milestone={milestone} -->"
    block = _project_progress_block(
        service,
        milestone=milestone,
        run_date=run_date,
        gate_records=gate_records,
        agent_records=agent_records,
        begin_marker=begin_marker,
        end_marker=end_marker,
    )
    base = existing or "# Project Progress\n"
    pattern = re.compile(
        rf"{re.escape(begin_marker)}\n.*?\n{re.escape(end_marker)}\n?",
        flags=re.DOTALL,
    )
    updated = pattern.sub(block, base) if pattern.search(base) else base.rstrip() + "\n\n" + block
    return updated if updated.endswith("\n") else updated + "\n"


_OPTIONAL_EVENT_FIELDS = (
    ("ack_of", "ack_of", False),
    ("branch", "branch", False),
    ("result", "result", False),
    ("report_commit", "report_commit", False),
    ("report_path", "report_path", False),
    ("source_message_id", "source_msg_id", False),
    ("last_seen_gate", "last_seen_gate", False),
    ("sync_role", "sync_role", True),
    ("allowed_role", "allowed_role", True),
    ("command_name", "command_name", False),
    ("target_role", "target_role", True),
)


def _audit_snapshot(
    service: CoordService,
    milestone: str,
    milestone_rec: CoordRecord,
) -> dict[str, Any]:
    run_date = _require_run_date(milestone_rec, milestone)
    events = _sorted_events(service, milestone)
    heartbeat_path = service.paths.log_dir(milestone, run_date) / "heartbeat_events.jsonl"
    logged_events, latest_logged_event_seq = _logged_event_stats(heartbeat_path)
    gate_records = coord_records(service, milestone, kind="gate")
    message_records = coord_records(service, milestone, kind="message")
    return {
        "milestone": milestone,
        "run_date": run_date,
        "received_events": len(events),
        "logged_events": logged_events,
        "latest_event_seq": events[-1].metadata_int("event_seq") if events else 0,
        "latest_logged_event_seq": latest_logged_event_seq,
        "reconciled": len(events) == logged_events,
        "pending_ack_messages": _pending_ack_messages(message_records, gate_records),
        "open_gates": _open_gates(gate_records),
        "log_pending_events": [
            event_projection(rec) for rec in events if rec.metadata_str("event") == "LOG_PENDING"
        ],
        "projection_path": str(heartbeat_path.relative_to(service.paths.workspace_root)),
    }


def _require_run_date(milestone_rec: CoordRecord, milestone: str) -> str:
    run_date = milestone_rec.metadata_str("run_date")
    if run_date:
        return run_date
    raise CoordError(f"milestone {milestone} does not have run_date metadata")


def _sorted_events(service: CoordService, milestone: str) -> list[CoordRecord]:
    return sorted(
        coord_records(service, milestone, kind="event"),
        key=lambda rec: (rec.metadata_int("event_seq"), rec.record_id),
    )


def _logged_event_stats(heartbeat_path: Path) -> tuple[int, int]:
    if not heartbeat_path.exists():
        return (0, 0)
    logged_events = 0
    latest_logged_event_seq = 0
    for line in heartbeat_path.read_text("utf-8").splitlines():
        if not line.strip():
            continue
        logged_events += 1
        payload = json.loads(line)
        if isinstance(payload, dict):
            latest_logged_event_seq = max(
                latest_logged_event_seq,
                int(payload.get("event_seq") or 0),
            )
    return (logged_events, latest_logged_event_seq)


def _pending_ack_messages(
    message_records: list[CoordRecord],
    gate_records: list[CoordRecord],
) -> list[dict[str, str]]:
    gate_states = {
        rec.metadata_str("gate_id"): rec.metadata_str("gate_state", "pending")
        for rec in gate_records
    }
    messages = [
        {
            "command": rec.metadata_str("command"),
            "role": rec.metadata_str("role"),
            "gate": rec.metadata_str("gate_id"),
            "phase": rec.metadata_str("phase"),
            "target_commit": rec.metadata_str("target_commit"),
        }
        for rec in message_records
        if rec.metadata_bool("requires_ack")
        and not rec.metadata_bool("effective")
        and gate_states.get(rec.metadata_str("gate_id"), "pending") != "closed"
    ]
    return sorted(
        messages,
        key=lambda payload: (
            payload["phase"],
            payload["gate"],
            payload["role"],
            payload["command"],
        ),
    )


def _open_gates(gate_records: list[CoordRecord]) -> list[dict[str, str]]:
    gates = [
        {
            "gate": rec.metadata_str("gate_id"),
            "phase": rec.metadata_str("phase"),
            "status": rec.metadata_str("gate_state"),
            "allowed_role": rec.metadata_str("allowed_role"),
            "target_commit": rec.metadata_str("target_commit"),
        }
        for rec in gate_records
        if rec.metadata_str("gate_state") != "closed"
    ]
    return sorted(gates, key=lambda payload: (payload["phase"], payload["gate"]))


def _write_heartbeat_projection(path: Path, events: list[CoordRecord]) -> None:
    lines = [json.dumps(event_projection(rec), ensure_ascii=False) for rec in events]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), "utf-8")


def _write_project_progress(
    service: CoordService,
    milestone: str,
    run_date: str,
    gate_records: list[CoordRecord],
    agent_records: list[CoordRecord],
) -> None:
    service.paths.progress_file.parent.mkdir(parents=True, exist_ok=True)
    existing = (
        service.paths.progress_file.read_text("utf-8")
        if service.paths.progress_file.exists()
        else ""
    )
    service.paths.progress_file.write_text(
        render_project_progress(
            service,
            milestone=milestone,
            run_date=run_date,
            gate_records=gate_records,
            agent_records=agent_records,
            existing=existing,
        ),
        "utf-8",
    )


def _ensure_review_event(
    *,
    records: list[CoordRecord],
    gate_id: str,
    phase: str,
    result: str,
    report_commit: str,
    report_path: str,
) -> None:
    review_event = find_matching_event(
        records,
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
    service: CoordService,
    *,
    milestone: str,
    run_date: str,
    expected_events: int,
) -> None:
    heartbeat_path = service.paths.log_dir(milestone, run_date) / "heartbeat_events.jsonl"
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


def _ensure_report_visible(service: CoordService, *, report_commit: str, report_path: str) -> None:
    report_relpath = _repo_relative_path(service, report_path)
    _git_cat_file(service, f"{report_commit}^{{commit}}")
    _git_cat_file(service, f"{report_commit}:{report_relpath}")


def _repo_relative_path(service: CoordService, report_path: str) -> str:
    candidate = Path(report_path)
    if not candidate.is_absolute():
        return str(candidate)
    try:
        return str(candidate.resolve().relative_to(service.paths.workspace_root))
    except ValueError as exc:
        raise CoordError(f"report_path must be inside workspace: {report_path}") from exc


def _git_cat_file(service: CoordService, object_name: str) -> None:
    try:
        subprocess.run(
            ["git", "cat-file", "-e", object_name],
            cwd=service.paths.workspace_root,
            capture_output=True,
            check=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip() or exc.stdout.strip()
        raise CoordError(f"git cat-file -e {object_name} failed: {stderr}") from exc


def _ensure_milestone_can_close(snapshot: dict[str, Any]) -> None:
    if not snapshot["reconciled"]:
        raise CoordError(
            "cannot close milestone before event reconciliation: "
            f"received_events={snapshot['received_events']} "
            f"logged_events={snapshot['logged_events']}"
        )
    if snapshot["open_gates"]:
        open_gate_ids = ", ".join(gate["gate"] for gate in snapshot["open_gates"])
        raise CoordError(f"cannot close milestone while gates remain open: {open_gate_ids}")
    if snapshot["pending_ack_messages"]:
        raise CoordError("cannot close milestone while pending ACK messages remain")


def _close_records(service: CoordService, records: list[CoordRecord]) -> None:
    for rec in records:
        if rec.status != "closed":
            service.store.update_record(rec.record_id, status="closed")


def _gate_state_rows(gate_records: list[CoordRecord]) -> list[str]:
    sorted_gates = sorted(
        gate_records,
        key=lambda rec: (_phase_sort_key(rec.metadata_str("phase")), rec.metadata_str("gate_id")),
    )
    return [_gate_state_row(gate) for gate in sorted_gates]


def _gate_state_row(gate: CoordRecord) -> str:
    cells = [
        gate.metadata_str("gate_id"),
        gate.metadata_str("phase"),
        gate.metadata_str("gate_state", "pending"),
        gate.metadata_str("result") or "",
        gate.metadata_str("opened_at"),
        gate.metadata_str("closed_at"),
        gate.metadata_str("target_commit"),
        _gate_report(gate),
    ]
    return "| " + " | ".join(cells) + " |"


def _gate_report(gate: CoordRecord) -> str:
    report_path = gate.metadata_str("report_path")
    report_commit = gate.metadata_str("report_commit")
    if report_path and report_commit:
        return f"{report_path} ({report_commit})"
    return report_path


def _watchdog_rows(agent_records: list[CoordRecord]) -> list[str]:
    return [_watchdog_row(agent) for agent in _visible_agents(agent_records)]


def _visible_agents(agent_records: list[CoordRecord]) -> list[CoordRecord]:
    non_pm_agents = sorted(
        (rec for rec in agent_records if rec.metadata_str("role") != "pm"),
        key=lambda rec: rec.metadata_str("role"),
    )
    return non_pm_agents or sorted(agent_records, key=lambda rec: rec.metadata_str("role"))


def _watchdog_row(agent: CoordRecord) -> str:
    cells = [
        agent.metadata_str("role"),
        agent.metadata_str("agent_state"),
        agent.metadata_str("last_activity"),
        agent.metadata_str("current_task"),
        agent.metadata_str("stale_risk", "none"),
        agent.metadata_str("action"),
    ]
    return "| " + " | ".join(cells) + " |"


def _project_progress_block(
    service: CoordService,
    *,
    milestone: str,
    run_date: str,
    gate_records: list[CoordRecord],
    agent_records: list[CoordRecord],
    begin_marker: str,
    end_marker: str,
) -> str:
    latest = latest_gate(gate_records)
    non_pm_agents = [
        rec for rec in _visible_agents(agent_records) if rec.metadata_str("role") != "pm"
    ]
    summary = _progress_summary(service, milestone, run_date, latest, non_pm_agents)
    lines = [
        begin_marker,
        f"## {run_date} (generated) | {milestone.upper()}",
        f"- Status: {summary['status']}",
        f"- Done: {summary['done']}",
        f"- Evidence: {summary['evidence']}",
        f"- Next: {summary['next']}",
        f"- Risk: {summary['risk']}",
        end_marker,
    ]
    return "\n".join(lines) + "\n"


def _progress_summary(
    service: CoordService,
    milestone: str,
    run_date: str,
    latest: CoordRecord | None,
    non_pm_agents: list[CoordRecord],
) -> dict[str, str]:
    return {
        "status": _progress_status(latest),
        "done": _done_summary(latest, non_pm_agents),
        "evidence": ", ".join(_evidence_parts(service, milestone, run_date, latest)),
        "next": _next_step(milestone, latest),
        "risk": _risk_summary(latest, non_pm_agents),
    }


def _progress_status(latest: CoordRecord | None) -> str:
    if latest is None:
        return "in_progress"
    gate_state = latest.metadata_str("gate_state", "pending")
    gate_result = latest.metadata_str("result")
    if gate_state == "closed" and gate_result in {"PASS", "PASS_WITH_RISK"}:
        return "done"
    return "in_progress"


def _done_summary(latest: CoordRecord | None, agents: list[CoordRecord]) -> str:
    if latest is None:
        return "control plane initialized"
    gate_id = latest.metadata_str("gate_id")
    gate_state = latest.metadata_str("gate_state", "pending")
    gate_result = latest.metadata_str("result")
    agent_summary = ", ".join(
        f"{agent.metadata_str('role')}={agent.metadata_str('agent_state')}"
        for agent in agents
    ) or "no-agents"
    result_suffix = f" ({gate_result})" if gate_result else ""
    return f"最新 gate {gate_id} 为 {gate_state}{result_suffix}；{agent_summary}"


def _evidence_parts(
    service: CoordService,
    milestone: str,
    run_date: str,
    latest: CoordRecord | None,
) -> list[str]:
    phase_subdir = service.paths.phase_subdir(milestone)
    evidence = [
        f"`dev_docs/logs/{phase_subdir}/{milestone}_{run_date}/gate_state.md`",
        f"`dev_docs/logs/{phase_subdir}/{milestone}_{run_date}/watchdog_status.md`",
    ]
    if latest is None:
        return evidence
    report_path = latest.metadata_str("report_path")
    report_commit = latest.metadata_str("report_commit")
    if report_path and report_commit:
        evidence.append(f"`{report_path}` ({report_commit})")
    return evidence


def _next_step(milestone: str, latest: CoordRecord | None) -> str:
    if latest is None:
        return f"等待 {milestone.upper()} 下一条 gate 或 phase 指令"
    gate_id = latest.metadata_str("gate_id")
    gate_state = latest.metadata_str("gate_state", "pending")
    if gate_state == "open":
        allowed_role = latest.metadata_str("allowed_role") or "unknown"
        return f"继续推进 {gate_id}，当前 allowed_role={allowed_role}"
    if gate_state == "closed":
        return f"{gate_id} 已关闭，等待 {milestone.upper()} 下一条 gate"
    return f"等待 {milestone.upper()} 下一条 gate 或 phase 指令"


def _risk_summary(latest: CoordRecord | None, agents: list[CoordRecord]) -> str:
    stale_agents = [
        agent.metadata_str("role")
        for agent in agents
        if agent.metadata_str("stale_risk", "none") != "none"
    ]
    if stale_agents:
        return f"stale_risk={'/'.join(stale_agents)}"
    if latest is None:
        return "无"
    gate_id = latest.metadata_str("gate_id")
    gate_result = latest.metadata_str("result")
    if gate_result == "FAIL":
        return f"{gate_id} 关闭结果为 FAIL"
    if gate_result == "PASS_WITH_RISK":
        return f"{gate_id} 关闭结果为 PASS_WITH_RISK"
    return "无"
