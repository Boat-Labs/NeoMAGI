from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionIdentity:
    """Minimal identity for scope resolution.

    M3: session_id + channel_type + channel_id are the active fields.
    M4: peer_id, account_id become active for per-peer/per-account scopes.
    """

    session_id: str
    channel_type: str = "dm"  # "dm" | "telegram" | ...
    channel_id: str | None = None  # group chat channel ID (for session key routing)
    peer_id: str | None = None  # M4: per-peer isolation
    account_id: str | None = None  # M4: per-account isolation


def resolve_scope_key(identity: SessionIdentity, dm_scope: str = "main") -> str:
    """Pure function: identity + dm_scope → scope_key.

    M3: dm_scope is guaranteed to be 'main' by SessionSettings validator.
    Non-main values raise ValueError (fail-fast, no silent fallback).

    M4 extension points (after SessionSettings validator is relaxed):
    - 'per-peer' → f"peer:{identity.peer_id}"
    - 'per-channel-peer' → f"{identity.channel_type}:peer:{identity.peer_id}"
    - 'per-account-channel-peer' →
        f"{identity.account_id}:{identity.channel_type}:peer:{identity.peer_id}"
    """
    if dm_scope == "main":
        return "main"
    # M3: this branch should never be reached (guarded by SessionSettings validator).
    # Fail-fast if it does — never silently degrade to main.
    raise ValueError(
        f"dm_scope '{dm_scope}' is not supported in M3. "
        "Only 'main' is allowed. See ADR 0034."
    )


def resolve_session_key(identity: SessionIdentity, dm_scope: str = "main") -> str:
    """Pure function: identity + dm_scope → session storage key.

    Key semantics (aligned with existing manager.py:resolve_session):
    - DM → scope_key (from resolve_scope_key)
    - Group → 'group:{channel_id}' (channel_id from identity, NOT session_id)
    """
    if identity.channel_type == "dm":
        return resolve_scope_key(identity, dm_scope)
    if identity.channel_id is None:
        raise ValueError("channel_id is required for non-DM session key resolution")
    return f"group:{identity.channel_id}"
