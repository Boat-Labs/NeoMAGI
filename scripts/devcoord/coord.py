from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    _REPO_ROOT = Path(__file__).resolve().parents[2]
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from scripts.devcoord.coord_actions import execute_action
    from scripts.devcoord.coord_parser import build_parser
    from scripts.devcoord.model import (
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
    from .coord_actions import execute_action
    from .coord_parser import build_parser
    from .model import (
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
    "send-command": lambda a: {
        "milestone": a.milestone,
        "role": a.role,
        "phase": a.phase,
        "gate_id": a.gate,
        "task": a.task,
        "target_commit": _none_if_placeholder(a.target_commit),
        "command_name": a.name,
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


# CLI entry
# ---------------------------------------------------------------------------


def _effective_action(args: argparse.Namespace) -> str:
    """Derive the canonical action name from parsed args.

    Both grouped CLI (``coord.py init``) and structured payload
    (``coord.py apply init``) resolve to the same ``"init"`` string,
    so downstream logic has a single decision point.
    """
    if args.command == "apply":
        return args.action
    return args._action


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
        parser = build_parser()
        args = parser.parse_args(normalized)
        action = _effective_action(args)
        resolved_paths = paths or _resolve_paths()
        resolved_store = store or SQLiteCoordStore(resolved_paths.control_db)
        service = CoordService(
            paths=resolved_paths,
            store=resolved_store,
            now_fn=now_fn or _utc_now,
        )
        if args.command == "apply":
            execute_action(service, action, _load_payload(args))
        else:
            payload = _PAYLOAD_BUILDERS[action](args)
            execute_action(service, action, payload)
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


if __name__ == "__main__":
    raise SystemExit(main())
