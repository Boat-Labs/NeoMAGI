from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def _normalize_milestone(value: str) -> str:
    return value.strip().lower()


def _normalize_role(value: str) -> str:
    return value.strip().lower()


def _none_if_placeholder(value: str | None) -> str | None:
    if value is None:
        return None
    if value.strip().lower() in {"", "-", "na", "none", "null"}:
        return None
    return value


def _merge_dicts(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    merged.update(updates)
    return merged


def _phase_sort_key(value: str) -> tuple[int, str]:
    if value.isdigit():
        return (0, f"{int(value):08d}")
    return (1, value)


def _render_role(role: str) -> str:
    return "PM" if role == "pm" else role


def _to_agent_state(status: str) -> str:
    normalized = status.strip().lower()
    valid = {"idle", "spawning", "running", "working", "stuck", "done", "stopped", "dead"}
    if normalized in valid:
        return normalized
    return "working"


def _utc_now() -> str:
    return datetime.now(tz=UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
