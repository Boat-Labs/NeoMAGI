from __future__ import annotations

import argparse
import json
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
        CoordError,
        CoordPaths,
        _git_output,
    )
    from scripts.devcoord.service import CoordService, _none_if_placeholder, _utc_now
    from scripts.devcoord.sqlite_store import SQLiteCoordStore
    from scripts.devcoord.store import (
        CoordStore,
        MemoryCoordStore,
    )
else:
    from .model import (
        DEFAULT_ROLES,
        CoordError,
        CoordPaths,
        _git_output,
    )
    from .service import CoordService, _none_if_placeholder, _utc_now
    from .sqlite_store import SQLiteCoordStore
    from .store import (
        CoordStore,
        MemoryCoordStore,
    )

__all__ = [
    "CoordError",
    "CoordPaths",
    "CoordService",
    "CoordStore",
    "MemoryCoordStore",
    "SQLiteCoordStore",
    "_normalize_argv",
    "build_parser",
    "main",
    "run_cli",
    "_resolve_paths",
]

_RETIRED_FLAGS = frozenset({"--backend", "--beads-dir", "--bd-bin", "--dolt-bin"})

# ---------------------------------------------------------------------------
# Argv normalization: flat alias -> grouped canonical form
# ---------------------------------------------------------------------------

_FLAT_ALIAS_MAP: dict[str, list[str]] = {
    "open-gate": ["gate", "open"],
    "ack": ["command", "ack"],
    "heartbeat": ["event", "heartbeat"],
    "phase-complete": ["event", "phase-complete"],
    "recovery-check": ["event", "recovery-check"],
    "state-sync-ok": ["event", "state-sync-ok"],
    "ping": ["command", "send", "--name", "PING"],
    "unconfirmed-instruction": ["event", "unconfirmed-instruction"],
    "log-pending": ["event", "log-pending"],
    "stale-detected": ["event", "stale-detected"],
    "gate-review": ["gate", "review"],
    "gate-close": ["gate", "close"],
    "render": ["projection", "render"],
    "audit": ["projection", "audit"],
    "milestone-close": ["milestone", "close"],
}


def _normalize_argv(argv: Sequence[str]) -> list[str]:
    """Rewrite legacy flat commands to grouped canonical tokens.

    Retired flags (--backend, --beads-dir, --bd-bin, --dolt-bin) trigger
    an immediate error instead of being silently skipped.
    """
    result = list(argv)
    for token in result:
        bare = token.split("=", 1)[0] if "=" in token else token
        if bare in _RETIRED_FLAGS:
            raise CoordError(
                f"{bare} has been retired. "
                "The devcoord control plane now uses SQLite exclusively "
                "(.devcoord/control.db). Remove the flag and use the "
                "canonical grouped CLI (e.g. gate open, projection render)."
            )
    idx = 0
    while idx < len(result):
        token = result[idx]
        if token.startswith("-"):
            idx += 1
            continue
        break
    if idx < len(result) and result[idx] in _FLAT_ALIAS_MAP:
        return result[:idx] + _FLAT_ALIAS_MAP[result[idx]] + result[idx + 1:]
    return result


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="NeoMAGI devcoord control plane (SQLite-only, .devcoord/control.db)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- init (top-level) ---
    init_p = subparsers.add_parser("init", help="Initialize the shared control plane")
    init_p.set_defaults(_action="init")
    init_p.add_argument("--milestone", required=True)
    init_p.add_argument("--run-date", default=date.today().isoformat())
    init_p.add_argument("--roles", default=",".join(DEFAULT_ROLES))

    # --- gate group ---
    gate_p = subparsers.add_parser("gate", help="Gate lifecycle commands")
    gate_sub = gate_p.add_subparsers(dest="subcommand", required=True)

    gate_open_p = gate_sub.add_parser("open", help="Create a pending GATE_OPEN command")
    gate_open_p.set_defaults(_action="open-gate")
    gate_open_p.add_argument("--milestone", required=True)
    gate_open_p.add_argument("--phase", required=True)
    gate_open_p.add_argument("--gate", required=True)
    gate_open_p.add_argument("--allowed-role", required=True)
    gate_open_p.add_argument("--target-commit", required=True)
    gate_open_p.add_argument("--task", default="gate open pending")

    gate_review_p = gate_sub.add_parser("review", help="Record a GATE_REVIEW_COMPLETE event")
    gate_review_p.set_defaults(_action="gate-review")
    gate_review_p.add_argument("--milestone", required=True)
    gate_review_p.add_argument("--role", required=True)
    gate_review_p.add_argument("--phase", required=True)
    gate_review_p.add_argument("--gate", required=True)
    gate_review_p.add_argument("--result", required=True)
    gate_review_p.add_argument("--report-commit", required=True)
    gate_review_p.add_argument("--report-path", required=True)
    gate_review_p.add_argument("--task", required=True)

    gate_close_p = gate_sub.add_parser("close", help="Close a gate after review is complete")
    gate_close_p.set_defaults(_action="gate-close")
    gate_close_p.add_argument("--milestone", required=True)
    gate_close_p.add_argument("--phase", required=True)
    gate_close_p.add_argument("--gate", required=True)
    gate_close_p.add_argument("--result", required=True)
    gate_close_p.add_argument("--report-commit", required=True)
    gate_close_p.add_argument("--report-path", required=True)
    gate_close_p.add_argument("--task", required=True)

    # --- command group ---
    cmd_p = subparsers.add_parser("command", help="Command dispatch (ack, send)")
    cmd_sub = cmd_p.add_subparsers(dest="subcommand", required=True)

    cmd_ack_p = cmd_sub.add_parser("ack", help="ACK a pending command and mark it effective")
    cmd_ack_p.set_defaults(_action="ack")
    cmd_ack_p.add_argument("--milestone", required=True)
    cmd_ack_p.add_argument("--role", required=True)
    cmd_ack_p.add_argument("--cmd", required=True)
    cmd_ack_p.add_argument("--gate", required=True)
    cmd_ack_p.add_argument("--commit", required=True)
    cmd_ack_p.add_argument("--phase")
    cmd_ack_p.add_argument("--task", default="ACK command")

    cmd_send_p = cmd_sub.add_parser("send", help="Send a named command (currently: PING)")
    cmd_send_p.set_defaults(_action="ping")
    cmd_send_p.add_argument("--name", required=True, choices=["PING"])
    cmd_send_p.add_argument("--milestone", required=True)
    cmd_send_p.add_argument("--role", required=True)
    cmd_send_p.add_argument("--phase", required=True)
    cmd_send_p.add_argument("--gate", required=True)
    cmd_send_p.add_argument("--task", required=True)
    cmd_send_p.add_argument("--target-commit")

    # --- event group ---
    event_p = subparsers.add_parser("event", help="Protocol events")
    event_sub = event_p.add_subparsers(dest="subcommand", required=True)

    event_hb_p = event_sub.add_parser("heartbeat", help="Record a heartbeat event")
    event_hb_p.set_defaults(_action="heartbeat")
    event_hb_p.add_argument("--milestone", required=True)
    event_hb_p.add_argument("--role", required=True)
    event_hb_p.add_argument("--phase", required=True)
    event_hb_p.add_argument("--status", required=True)
    event_hb_p.add_argument("--task", required=True)
    event_hb_p.add_argument("--eta-min", type=int)
    event_hb_p.add_argument("--gate")
    event_hb_p.add_argument("--target-commit")
    event_hb_p.add_argument("--branch")

    event_pc_p = event_sub.add_parser("phase-complete", help="Record a PHASE_COMPLETE event")
    event_pc_p.set_defaults(_action="phase-complete")
    event_pc_p.add_argument("--milestone", required=True)
    event_pc_p.add_argument("--role", required=True)
    event_pc_p.add_argument("--phase", required=True)
    event_pc_p.add_argument("--gate", required=True)
    event_pc_p.add_argument("--commit", required=True)
    event_pc_p.add_argument("--task", required=True)
    event_pc_p.add_argument("--branch")

    event_rc_p = event_sub.add_parser(
        "recovery-check", help="Record a RECOVERY_CHECK event after restart or context loss"
    )
    event_rc_p.set_defaults(_action="recovery-check")
    event_rc_p.add_argument("--milestone", required=True)
    event_rc_p.add_argument("--role", required=True)
    event_rc_p.add_argument("--last-seen-gate", required=True)
    event_rc_p.add_argument("--task", required=True)

    event_ss_p = event_sub.add_parser(
        "state-sync-ok", help="Record a STATE_SYNC_OK response from PM"
    )
    event_ss_p.set_defaults(_action="state-sync-ok")
    event_ss_p.add_argument("--milestone", required=True)
    event_ss_p.add_argument("--role", required=True)
    event_ss_p.add_argument("--gate", required=True)
    event_ss_p.add_argument("--target-commit", required=True)
    event_ss_p.add_argument("--task", required=True)

    event_sd_p = event_sub.add_parser(
        "stale-detected", help="Record a suspected stale role after timeout checks"
    )
    event_sd_p.set_defaults(_action="stale-detected")
    event_sd_p.add_argument("--milestone", required=True)
    event_sd_p.add_argument("--role", required=True)
    event_sd_p.add_argument("--phase", required=True)
    event_sd_p.add_argument("--task", required=True)
    event_sd_p.add_argument("--gate")
    event_sd_p.add_argument("--target-commit")
    event_sd_p.add_argument("--ping-count", type=int)

    event_lp_p = event_sub.add_parser(
        "log-pending", help="Record a LOG_PENDING event when append-first logging is deferred"
    )
    event_lp_p.set_defaults(_action="log-pending")
    event_lp_p.add_argument("--milestone", required=True)
    event_lp_p.add_argument("--phase", required=True)
    event_lp_p.add_argument("--task", required=True)
    event_lp_p.add_argument("--gate")
    event_lp_p.add_argument("--target-commit")

    event_ui_p = event_sub.add_parser(
        "unconfirmed-instruction",
        help="Record an unconfirmed instruction after repeated PING attempts",
    )
    event_ui_p.set_defaults(_action="unconfirmed-instruction")
    event_ui_p.add_argument("--milestone", required=True)
    event_ui_p.add_argument("--role", required=True)
    event_ui_p.add_argument("--cmd", required=True)
    event_ui_p.add_argument("--phase", required=True)
    event_ui_p.add_argument("--gate", required=True)
    event_ui_p.add_argument("--task", required=True)
    event_ui_p.add_argument("--target-commit")
    event_ui_p.add_argument("--ping-count", type=int)

    # --- projection group ---
    proj_p = subparsers.add_parser("projection", help="Projection rendering and audit")
    proj_sub = proj_p.add_subparsers(dest="subcommand", required=True)

    proj_render_p = proj_sub.add_parser("render", help="Render dev_docs projection files")
    proj_render_p.set_defaults(_action="render")
    proj_render_p.add_argument("--milestone", required=True)

    proj_audit_p = proj_sub.add_parser(
        "audit", help="Report append-first / projection reconciliation status"
    )
    proj_audit_p.set_defaults(_action="audit")
    proj_audit_p.add_argument("--milestone", required=True)

    # --- milestone group ---
    ms_p = subparsers.add_parser("milestone", help="Milestone lifecycle")
    ms_sub = ms_p.add_subparsers(dest="subcommand", required=True)

    ms_close_p = ms_sub.add_parser(
        "close", help="Close all control-plane records for a completed milestone"
    )
    ms_close_p.set_defaults(_action="milestone-close")
    ms_close_p.add_argument("--milestone", required=True)

    # --- apply (machine-first, unchanged) ---
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
            "milestone-close",
            "audit",
            "render",
        ),
    )
    apply_group = apply_parser.add_mutually_exclusive_group(required=True)
    apply_group.add_argument("--payload-file")
    apply_group.add_argument("--payload-stdin", action="store_true")

    return parser


# ---------------------------------------------------------------------------
# Canonical payload builders: argparse Namespace -> dict for _execute_action
# ---------------------------------------------------------------------------

_PAYLOAD_BUILDERS: dict[str, Callable[[argparse.Namespace], dict[str, Any]]] = {
    "init": lambda a: {
        "milestone": a.milestone,
        "run_date": a.run_date,
        "roles": _split_csv(a.roles),
    },
    "open-gate": lambda a: {
        "milestone": a.milestone,
        "phase": a.phase,
        "gate_id": a.gate,
        "allowed_role": a.allowed_role,
        "target_commit": a.target_commit,
        "task": a.task,
    },
    "ack": lambda a: {
        "milestone": a.milestone,
        "role": a.role,
        "command": a.cmd,
        "gate_id": a.gate,
        "commit": a.commit,
        "phase": a.phase,
        "task": a.task,
    },
    "heartbeat": lambda a: {
        "milestone": a.milestone,
        "role": a.role,
        "phase": a.phase,
        "status": a.status,
        "task": a.task,
        "eta_min": a.eta_min,
        "gate_id": _none_if_placeholder(a.gate),
        "target_commit": _none_if_placeholder(a.target_commit),
        "branch": _none_if_placeholder(a.branch),
    },
    "phase-complete": lambda a: {
        "milestone": a.milestone,
        "role": a.role,
        "phase": a.phase,
        "gate_id": a.gate,
        "commit": a.commit,
        "task": a.task,
        "branch": _none_if_placeholder(a.branch),
    },
    "recovery-check": lambda a: {
        "milestone": a.milestone,
        "role": a.role,
        "last_seen_gate": a.last_seen_gate,
        "task": a.task,
    },
    "state-sync-ok": lambda a: {
        "milestone": a.milestone,
        "role": a.role,
        "gate_id": a.gate,
        "target_commit": a.target_commit,
        "task": a.task,
    },
    "ping": lambda a: {
        "milestone": a.milestone,
        "role": a.role,
        "phase": a.phase,
        "gate_id": a.gate,
        "task": a.task,
        "target_commit": _none_if_placeholder(a.target_commit),
    },
    "unconfirmed-instruction": lambda a: {
        "milestone": a.milestone,
        "role": a.role,
        "command": a.cmd,
        "phase": a.phase,
        "gate_id": a.gate,
        "task": a.task,
        "target_commit": _none_if_placeholder(a.target_commit),
        "ping_count": a.ping_count,
    },
    "log-pending": lambda a: {
        "milestone": a.milestone,
        "phase": a.phase,
        "task": a.task,
        "gate_id": _none_if_placeholder(a.gate),
        "target_commit": _none_if_placeholder(a.target_commit),
    },
    "stale-detected": lambda a: {
        "milestone": a.milestone,
        "role": a.role,
        "phase": a.phase,
        "task": a.task,
        "gate_id": _none_if_placeholder(a.gate),
        "target_commit": _none_if_placeholder(a.target_commit),
        "ping_count": a.ping_count,
    },
    "gate-review": lambda a: {
        "milestone": a.milestone,
        "role": a.role,
        "phase": a.phase,
        "gate_id": a.gate,
        "result": a.result,
        "report_commit": a.report_commit,
        "report_path": a.report_path,
        "task": a.task,
    },
    "gate-close": lambda a: {
        "milestone": a.milestone,
        "phase": a.phase,
        "gate_id": a.gate,
        "result": a.result,
        "report_commit": a.report_commit,
        "report_path": a.report_path,
        "task": a.task,
    },
    "milestone-close": lambda a: {"milestone": a.milestone},
    "audit": lambda a: {"milestone": a.milestone},
    "render": lambda a: {"milestone": a.milestone},
}


# ---------------------------------------------------------------------------
# Action dispatch
# ---------------------------------------------------------------------------


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
    if command == "milestone-close":
        service.close_milestone(_require_payload_str(payload, "milestone"))
        return
    if command == "audit":
        payload_milestone = _require_payload_str(payload, "milestone")
        print(json.dumps(service.audit(payload_milestone), ensure_ascii=False))
        return
    if command == "render":
        service.render(_require_payload_str(payload, "milestone"))
        return
    raise CoordError(f"unsupported action: {command}")


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    store: CoordStore | None = None,
    paths: CoordPaths | None = None,
    now_fn: Callable[[], str] | None = None,
) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    try:
        normalized = _normalize_argv(raw_argv)
    except CoordError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    parser = build_parser()
    args = parser.parse_args(normalized)
    resolved_paths = paths or _resolve_paths()
    resolved_store = store or SQLiteCoordStore(resolved_paths.control_db)
    service = CoordService(
        paths=resolved_paths,
        store=resolved_store,
        now_fn=now_fn or _utc_now,
    )
    try:
        if args.command == "apply":
            _execute_action(service, args.action, _load_payload(args))
        else:
            action = args._action
            payload = _PAYLOAD_BUILDERS[action](args)
            _execute_action(service, action, payload)
    except CoordError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    return run_cli()


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def _resolve_paths() -> CoordPaths:
    git_common_dir = _resolve_git_common_dir(Path.cwd())
    workspace_root = _shared_workspace_root(Path.cwd())
    control_root = workspace_root / ".devcoord"
    control_db = control_root / "control.db"

    # Guard: detect legacy beads control plane that could cause split-brain.
    # If a beads control plane exists (repo-root .beads/ or legacy
    # .coord/beads/) but no SQLite control.db has been bootstrapped yet,
    # refuse to proceed — the operator must complete the cutover first.
    if not control_db.exists():
        legacy_markers = (
            workspace_root / ".beads" / "metadata.json",
            workspace_root / ".coord" / "beads" / ".beads" / "metadata.json",
        )
        for marker in legacy_markers:
            if marker.exists():
                raise CoordError(
                    f"Legacy beads control plane detected at {marker.parent.relative_to(workspace_root)}/ "
                    "but no .devcoord/control.db exists yet. To avoid split-brain, "
                    "complete the Stage D cutover checklist "
                    "(see dev_docs/devcoord/sqlite_control_plane_runtime.md §7) "
                    "before running devcoord commands. "
                    "If the legacy beads data is no longer active, remove it first."
                )

    return CoordPaths(
        workspace_root=workspace_root,
        git_common_dir=git_common_dir,
        control_root=control_root,
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


# ---------------------------------------------------------------------------
# Helpers (unchanged)
# ---------------------------------------------------------------------------


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
