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

    Supported scopes:
    - 'main' → "main" (global shared session)
    - 'per-channel-peer' → "{channel_type}:peer:{peer_id}" (M4 Telegram default)
    - 'per-peer' → "peer:{peer_id}" (cross-channel peer isolation)
    """
    if dm_scope == "main":
        return "main"
    if dm_scope == "per-channel-peer":
        if identity.peer_id is None:
            raise ValueError("peer_id required for per-channel-peer")
        return f"{identity.channel_type}:peer:{identity.peer_id}"
    if dm_scope == "per-peer":
        if identity.peer_id is None:
            raise ValueError("peer_id required for per-peer")
        return f"peer:{identity.peer_id}"
    raise ValueError(f"Unsupported dm_scope: '{dm_scope}'")


def resolve_session_key(identity: SessionIdentity, dm_scope: str = "main") -> str:
    """Pure function: identity + dm_scope → session storage key.

    Key semantics:
    - DM (channel_id is None) → scope_key (from resolve_scope_key)
    - Group (channel_id is set) → 'group:{channel_id}'
    """
    if identity.channel_id is None:
        return resolve_scope_key(identity, dm_scope)
    return f"group:{identity.channel_id}"
