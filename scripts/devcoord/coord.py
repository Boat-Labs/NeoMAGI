from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from scripts.devcoord.model import (
        DEFAULT_ROLES,
        LEGACY_BEADS_SUBDIR,
        CliIssueStore,
        CoordError,
        CoordPaths,
        IssueStore,
        MemoryIssueStore,
        _git_output,
    )
    from scripts.devcoord.service import CoordService, _none_if_placeholder, _utc_now
else:
    from .model import (
        DEFAULT_ROLES,
        LEGACY_BEADS_SUBDIR,
        CliIssueStore,
        CoordError,
        CoordPaths,
        IssueStore,
        MemoryIssueStore,
        _git_output,
    )
    from .service import CoordService, _none_if_placeholder, _utc_now

__all__ = [
    "CliIssueStore",
    "CoordError",
    "CoordPaths",
    "CoordService",
    "IssueStore",
    "MemoryIssueStore",
    "build_parser",
    "main",
    "run_cli",
    "_resolve_paths",
]

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

    recovery_check_parser = subparsers.add_parser(
        "recovery-check",
        help="Record a RECOVERY_CHECK event after restart or context loss",
    )
    recovery_check_parser.add_argument("--milestone", required=True)
    recovery_check_parser.add_argument("--role", required=True)
    recovery_check_parser.add_argument("--last-seen-gate", required=True)
    recovery_check_parser.add_argument("--task", required=True)

    state_sync_ok_parser = subparsers.add_parser(
        "state-sync-ok",
        help="Record a STATE_SYNC_OK response from PM",
    )
    state_sync_ok_parser.add_argument("--milestone", required=True)
    state_sync_ok_parser.add_argument("--role", required=True)
    state_sync_ok_parser.add_argument("--gate", required=True)
    state_sync_ok_parser.add_argument("--target-commit", required=True)
    state_sync_ok_parser.add_argument("--task", required=True)

    ping_parser = subparsers.add_parser(
        "ping",
        help="Send a PING message that requires ACK",
    )
    ping_parser.add_argument("--milestone", required=True)
    ping_parser.add_argument("--role", required=True)
    ping_parser.add_argument("--phase", required=True)
    ping_parser.add_argument("--gate", required=True)
    ping_parser.add_argument("--task", required=True)
    ping_parser.add_argument("--target-commit")

    unconfirmed_instruction_parser = subparsers.add_parser(
        "unconfirmed-instruction",
        help="Record an unconfirmed instruction after repeated PING attempts",
    )
    unconfirmed_instruction_parser.add_argument("--milestone", required=True)
    unconfirmed_instruction_parser.add_argument("--role", required=True)
    unconfirmed_instruction_parser.add_argument("--cmd", required=True)
    unconfirmed_instruction_parser.add_argument("--phase", required=True)
    unconfirmed_instruction_parser.add_argument("--gate", required=True)
    unconfirmed_instruction_parser.add_argument("--task", required=True)
    unconfirmed_instruction_parser.add_argument("--target-commit")
    unconfirmed_instruction_parser.add_argument("--ping-count", type=int)

    log_pending_parser = subparsers.add_parser(
        "log-pending",
        help="Record a LOG_PENDING event when append-first logging is deferred",
    )
    log_pending_parser.add_argument("--milestone", required=True)
    log_pending_parser.add_argument("--phase", required=True)
    log_pending_parser.add_argument("--task", required=True)
    log_pending_parser.add_argument("--gate")
    log_pending_parser.add_argument("--target-commit")

    stale_detected_parser = subparsers.add_parser(
        "stale-detected",
        help="Record a suspected stale role after timeout checks",
    )
    stale_detected_parser.add_argument("--milestone", required=True)
    stale_detected_parser.add_argument("--role", required=True)
    stale_detected_parser.add_argument("--phase", required=True)
    stale_detected_parser.add_argument("--task", required=True)
    stale_detected_parser.add_argument("--gate")
    stale_detected_parser.add_argument("--target-commit")
    stale_detected_parser.add_argument("--ping-count", type=int)

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
            "recovery-check",
            "state-sync-ok",
            "ping",
            "unconfirmed-instruction",
            "log-pending",
            "stale-detected",
            "gate-review",
            "gate-close",
            "audit",
            "render",
        ),
    )
    apply_group = apply_parser.add_mutually_exclusive_group(required=True)
    apply_group.add_argument("--payload-file")
    apply_group.add_argument("--payload-stdin", action="store_true")

    render_parser = subparsers.add_parser("render", help="Render dev_docs projection files")
    render_parser.add_argument("--milestone", required=True)
    audit_parser = subparsers.add_parser(
        "audit",
        help="Report append-first / projection reconciliation status",
    )
    audit_parser.add_argument("--milestone", required=True)
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
    if command == "recovery-check":
        service.recovery_check(
            _require_payload_str(payload, "milestone"),
            role=_require_payload_str(payload, "role"),
            last_seen_gate=_require_payload_str(payload, "last_seen_gate"),
            task=_require_payload_str(payload, "task"),
        )
        return
    if command == "state-sync-ok":
        service.state_sync_ok(
            _require_payload_str(payload, "milestone"),
            role=_require_payload_str(payload, "role"),
            gate_id=_payload_alias(payload, "gate_id", "gate"),
            target_commit=_require_payload_str(payload, "target_commit"),
            task=_require_payload_str(payload, "task"),
        )
        return
    if command == "ping":
        service.ping(
            _require_payload_str(payload, "milestone"),
            role=_require_payload_str(payload, "role"),
            phase=_require_payload_str(payload, "phase"),
            gate_id=_payload_alias(payload, "gate_id", "gate"),
            task=_require_payload_str(payload, "task"),
            target_commit=_payload_str(payload, "target_commit", None),
        )
        return
    if command == "unconfirmed-instruction":
        ping_count = payload.get("ping_count")
        if ping_count is not None:
            ping_count = int(ping_count)
        service.unconfirmed_instruction(
            _require_payload_str(payload, "milestone"),
            role=_require_payload_str(payload, "role"),
            command=_payload_alias(payload, "command", "cmd"),
            phase=_require_payload_str(payload, "phase"),
            gate_id=_payload_alias(payload, "gate_id", "gate"),
            task=_require_payload_str(payload, "task"),
            target_commit=_payload_str(payload, "target_commit", None),
            ping_count=ping_count,
        )
        return
    if command == "log-pending":
        service.log_pending(
            _require_payload_str(payload, "milestone"),
            phase=_require_payload_str(payload, "phase"),
            task=_require_payload_str(payload, "task"),
            gate_id=_payload_str(payload, "gate_id", _payload_str(payload, "gate", None)),
            target_commit=_payload_str(payload, "target_commit", None),
        )
        return
    if command == "stale-detected":
        ping_count = payload.get("ping_count")
        if ping_count is not None:
            ping_count = int(ping_count)
        service.stale_detected(
            _require_payload_str(payload, "milestone"),
            role=_require_payload_str(payload, "role"),
            phase=_require_payload_str(payload, "phase"),
            task=_require_payload_str(payload, "task"),
            gate_id=_payload_str(payload, "gate_id", _payload_str(payload, "gate", None)),
            target_commit=_payload_str(payload, "target_commit", None),
            ping_count=ping_count,
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
    if command == "audit":
        payload_milestone = _require_payload_str(payload, "milestone")
        print(json.dumps(service.audit(payload_milestone), ensure_ascii=False))
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
        elif args.command == "recovery-check":
            _execute_action(
                service,
                "recovery-check",
                {
                    "milestone": args.milestone,
                    "role": args.role,
                    "last_seen_gate": args.last_seen_gate,
                    "task": args.task,
                },
            )
        elif args.command == "state-sync-ok":
            _execute_action(
                service,
                "state-sync-ok",
                {
                    "milestone": args.milestone,
                    "role": args.role,
                    "gate_id": args.gate,
                    "target_commit": args.target_commit,
                    "task": args.task,
                },
            )
        elif args.command == "ping":
            _execute_action(
                service,
                "ping",
                {
                    "milestone": args.milestone,
                    "role": args.role,
                    "phase": args.phase,
                    "gate_id": args.gate,
                    "task": args.task,
                    "target_commit": _none_if_placeholder(args.target_commit),
                },
            )
        elif args.command == "unconfirmed-instruction":
            _execute_action(
                service,
                "unconfirmed-instruction",
                {
                    "milestone": args.milestone,
                    "role": args.role,
                    "command": args.cmd,
                    "phase": args.phase,
                    "gate_id": args.gate,
                    "task": args.task,
                    "target_commit": _none_if_placeholder(args.target_commit),
                    "ping_count": args.ping_count,
                },
            )
        elif args.command == "log-pending":
            _execute_action(
                service,
                "log-pending",
                {
                    "milestone": args.milestone,
                    "phase": args.phase,
                    "task": args.task,
                    "gate_id": _none_if_placeholder(args.gate),
                    "target_commit": _none_if_placeholder(args.target_commit),
                },
            )
        elif args.command == "stale-detected":
            _execute_action(
                service,
                "stale-detected",
                {
                    "milestone": args.milestone,
                    "role": args.role,
                    "phase": args.phase,
                    "task": args.task,
                    "gate_id": _none_if_placeholder(args.gate),
                    "target_commit": _none_if_placeholder(args.target_commit),
                    "ping_count": args.ping_count,
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
        elif args.command == "audit":
            _execute_action(service, "audit", {"milestone": args.milestone})
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

if __name__ == "__main__":
    raise SystemExit(main())
