"""Tests for scope_resolver (Phase 0, ADR 0034)."""

from __future__ import annotations

import pytest

from src.session.scope_resolver import (
    SessionIdentity,
    resolve_scope_key,
    resolve_session_key,
)


class TestSessionIdentity:
    def test_defaults(self) -> None:
        si = SessionIdentity(session_id="s1")
        assert si.channel_type == "dm"
        assert si.channel_id is None
        assert si.peer_id is None
        assert si.account_id is None

    def test_frozen(self) -> None:
        si = SessionIdentity(session_id="s1")
        with pytest.raises(AttributeError):
            si.session_id = "s2"  # type: ignore[misc]


class TestResolveScopeKey:
    def test_main_returns_main(self) -> None:
        si = SessionIdentity(session_id="s1")
        assert resolve_scope_key(si, dm_scope="main") == "main"

    def test_default_dm_scope_is_main(self) -> None:
        si = SessionIdentity(session_id="s1")
        assert resolve_scope_key(si) == "main"

    def test_per_channel_peer(self) -> None:
        si = SessionIdentity(
            session_id="s1", channel_type="telegram", peer_id="123"
        )
        assert resolve_scope_key(si, dm_scope="per-channel-peer") == "telegram:peer:123"

    def test_per_peer(self) -> None:
        si = SessionIdentity(session_id="s1", peer_id="456")
        assert resolve_scope_key(si, dm_scope="per-peer") == "peer:456"

    def test_per_channel_peer_missing_peer_id_raises(self) -> None:
        si = SessionIdentity(session_id="s1", channel_type="telegram")
        with pytest.raises(ValueError, match="peer_id required for per-channel-peer"):
            resolve_scope_key(si, dm_scope="per-channel-peer")

    def test_per_peer_missing_peer_id_raises(self) -> None:
        si = SessionIdentity(session_id="s1")
        with pytest.raises(ValueError, match="peer_id required for per-peer"):
            resolve_scope_key(si, dm_scope="per-peer")

    def test_unsupported_scope_raises(self) -> None:
        si = SessionIdentity(session_id="s1")
        with pytest.raises(ValueError, match="Unsupported dm_scope"):
            resolve_scope_key(si, dm_scope="invalid-scope")

    def test_identity_fields_preserved(self) -> None:
        """M4 fields are accepted; main scope ignores peer_id."""
        si = SessionIdentity(
            session_id="s1",
            channel_type="telegram",
            channel_id="ch-1",
            peer_id="peer-1",
            account_id="acc-1",
        )
        assert resolve_scope_key(si, dm_scope="main") == "main"


class TestResolveSessionKey:
    def test_dm_returns_scope_key(self) -> None:
        si = SessionIdentity(session_id="s1", channel_type="dm")
        assert resolve_session_key(si, dm_scope="main") == "main"

    def test_group_returns_channel_id(self) -> None:
        si = SessionIdentity(
            session_id="s1", channel_type="telegram", channel_id="group-42"
        )
        assert resolve_session_key(si, dm_scope="main") == "group:group-42"

    def test_telegram_dm_uses_scope_path(self) -> None:
        """Telegram DM (channel_id=None) routes through resolve_scope_key."""
        si = SessionIdentity(
            session_id="s1", channel_type="telegram", peer_id="789"
        )
        assert (
            resolve_session_key(si, dm_scope="per-channel-peer")
            == "telegram:peer:789"
        )
