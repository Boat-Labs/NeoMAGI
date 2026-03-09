from __future__ import annotations

import argparse
from datetime import date
from typing import Any

from .model import DEFAULT_ROLES

_SEND_COMMAND_CHOICES = ("PING", "STOP", "WAIT", "RESUME")
_APPLY_ACTION_CHOICES = (
    "init",
    "open-gate",
    "ack",
    "heartbeat",
    "phase-complete",
    "recovery-check",
    "state-sync-ok",
    "ping",
    "send-command",
    "unconfirmed-instruction",
    "log-pending",
    "stale-detected",
    "gate-review",
    "gate-close",
    "milestone-close",
    "audit",
    "render",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="NeoMAGI devcoord control plane (SQLite-only, .devcoord/control.db)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_init_parser(subparsers)
    _add_gate_parser(subparsers)
    _add_command_parser(subparsers)
    _add_event_parser(subparsers)
    _add_projection_parser(subparsers)
    _add_milestone_parser(subparsers)
    _add_apply_parser(subparsers)
    return parser


def _add_init_parser(subparsers: Any) -> None:
    init_p = subparsers.add_parser("init", help="Initialize the shared control plane")
    init_p.set_defaults(_action="init")
    init_p.add_argument("--milestone", required=True)
    init_p.add_argument("--run-date", default=date.today().isoformat())
    init_p.add_argument("--roles", default=",".join(DEFAULT_ROLES))


def _add_gate_parser(subparsers: Any) -> None:
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


def _add_command_parser(subparsers: Any) -> None:
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

    cmd_send_p = cmd_sub.add_parser(
        "send", help="Send a named command (PING, STOP, WAIT, RESUME)"
    )
    cmd_send_p.set_defaults(_action="send-command")
    cmd_send_p.add_argument("--name", required=True, choices=_SEND_COMMAND_CHOICES)
    cmd_send_p.add_argument("--milestone", required=True)
    cmd_send_p.add_argument("--role", required=True)
    cmd_send_p.add_argument("--phase", required=True)
    cmd_send_p.add_argument("--gate", required=True)
    cmd_send_p.add_argument("--task", required=True)
    cmd_send_p.add_argument("--target-commit")


def _add_event_parser(subparsers: Any) -> None:
    event_p = subparsers.add_parser("event", help="Protocol events")
    event_sub = event_p.add_subparsers(dest="subcommand", required=True)
    _add_event_lifecycle_parsers(event_sub)
    _add_event_recovery_parsers(event_sub)
    _add_event_watchdog_parsers(event_sub)


def _add_event_lifecycle_parsers(event_sub: Any) -> None:
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


def _add_event_recovery_parsers(event_sub: Any) -> None:
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


def _add_event_watchdog_parsers(event_sub: Any) -> None:
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


def _add_projection_parser(subparsers: Any) -> None:
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


def _add_milestone_parser(subparsers: Any) -> None:
    ms_p = subparsers.add_parser("milestone", help="Milestone lifecycle")
    ms_sub = ms_p.add_subparsers(dest="subcommand", required=True)
    ms_close_p = ms_sub.add_parser(
        "close", help="Close all control-plane records for a completed milestone"
    )
    ms_close_p.set_defaults(_action="milestone-close")
    ms_close_p.add_argument("--milestone", required=True)


def _add_apply_parser(subparsers: Any) -> None:
    apply_parser = subparsers.add_parser(
        "apply",
        help="Execute a control-plane action from structured JSON payload",
    )
    apply_parser.add_argument("action", choices=_APPLY_ACTION_CHOICES)
    apply_group = apply_parser.add_mutually_exclusive_group(required=True)
    apply_group.add_argument("--payload-file")
    apply_group.add_argument("--payload-stdin", action="store_true")
