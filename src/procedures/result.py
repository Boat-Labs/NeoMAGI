"""Procedure-level ToolResult and normalization.

ToolResult is the canonical return type for procedure action execution.
Existing tools continue to return raw ``dict``; normalization only happens
inside ``ProcedureRuntime.apply_action()``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ToolResult(BaseModel):
    """Normalized result from a procedure action's underlying tool."""

    model_config = ConfigDict(frozen=True)

    ok: bool = True
    data: dict[str, Any] = Field(default_factory=dict)
    context_patch: dict[str, Any] = Field(default_factory=dict)


def normalize_tool_result(raw: dict[str, Any] | ToolResult) -> ToolResult:
    """Convert a raw dict or ToolResult into a canonical ToolResult.

    Rules:
    - If *raw* is already a ``ToolResult``, return as-is.
    - If *raw* is a dict containing ``context_patch``, extract it.
    - Remaining keys (minus ``context_patch`` and ``ok``) become ``data``.
    - ``ok`` defaults to ``True`` unless explicitly ``False`` in the dict.

    Does NOT mutate the input dict.
    """
    if isinstance(raw, ToolResult):
        return raw

    context_patch = raw.get("context_patch", {})
    ok = raw.get("ok", True)
    data = {k: v for k, v in raw.items() if k not in ("context_patch", "ok")}
    return ToolResult(ok=ok, data=data, context_patch=context_patch)
