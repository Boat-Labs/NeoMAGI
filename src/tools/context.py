from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolContext:
    """Runtime context injected into tool execution by AgentLoop.

    scope_key: resolved by session_resolver (ADR 0034). Tools MUST NOT
    re-derive scope from session_id; they consume this value directly.
    session_id: current session identifier (for audit/logging).
    """

    scope_key: str = "main"
    session_id: str = "main"
