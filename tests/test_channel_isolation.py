"""Tests for cross-channel isolation (P4, ADR 0034).

Covers:
- scope_key isolation: Telegram DM uses per-channel-peer, WebChat uses main
- session isolation: same/different Telegram users → same/different sessions
- memory scope isolation: Telegram flush scope ≠ WebChat scope
- tool mode / risk gating consistency: both channels use same mode
- flush persist scope consistency: Telegram flush scope_key = recall scope_key
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.agent import AgentLoop
from src.agent.events import TextChunk
from src.agent.memory_flush import MemoryFlushCandidate
from src.config.settings import GatewaySettings, MemorySettings, TelegramSettings
from src.gateway.budget_gate import Reservation
from src.gateway.dispatch import DEFAULT_RESERVE_EUR, dispatch_chat
from src.session.scope_resolver import SessionIdentity, resolve_scope_key, resolve_session_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_handle_message(*_args, **_kwargs):
    """Empty async generator."""
    return
    yield  # pragma: no cover


async def _text_handle_message(*_args, **_kwargs):
    """Async generator yielding a single TextChunk."""
    yield TextChunk(content="hello")


def _approved_reservation(rid: str = "res-1") -> Reservation:
    return Reservation(denied=False, reservation_id=rid, reserved_eur=DEFAULT_RESERVE_EUR)


def _make_dispatch_deps(*, handle_message_fn=None):
    """Build mock dependencies for dispatch_chat."""
    entry = MagicMock()
    entry.name = "openai"
    entry.model = "test-model"
    entry.agent_loop = MagicMock()
    entry.agent_loop.handle_message = handle_message_fn or _noop_handle_message

    registry = MagicMock()
    registry.get = MagicMock(return_value=entry)

    mgr = MagicMock()
    mgr.try_claim_session = AsyncMock(return_value="lock-1")
    mgr.load_session_from_db = AsyncMock()
    mgr.release_session = AsyncMock()

    gate = MagicMock()
    gate.try_reserve = AsyncMock(return_value=_approved_reservation())
    gate.settle = AsyncMock()

    return registry, mgr, gate, entry


def _make_agent_loop(tmp_path: Path, *, memory_settings: MemorySettings | None = None) -> AgentLoop:
    model_client = MagicMock()
    session_manager = MagicMock()
    session_manager.append_message = AsyncMock(return_value=MagicMock(seq=1))
    session_manager.get_mode = AsyncMock(return_value="chat_safe")
    session_manager.get_compaction_state = AsyncMock(return_value=None)
    session_manager.get_history_with_seq = MagicMock(return_value=[])

    return AgentLoop(
        model_client=model_client,
        session_manager=session_manager,
        workspace_dir=tmp_path,
        memory_settings=memory_settings,
    )


# ---------------------------------------------------------------------------
# 1. scope_key isolation
# ---------------------------------------------------------------------------


class TestScopeKeyIsolation:
    """Telegram DM scope_key = telegram:peer:{id}; WebChat scope_key = main."""

    def test_telegram_dm_scope_key(self) -> None:
        identity = SessionIdentity(
            session_id="", channel_type="telegram", peer_id="111"
        )
        assert resolve_scope_key(identity, dm_scope="per-channel-peer") == "telegram:peer:111"

    def test_webchat_scope_key(self) -> None:
        identity = SessionIdentity(session_id="main", channel_type="dm")
        assert resolve_scope_key(identity, dm_scope="main") == "main"

    def test_telegram_and_webchat_scopes_differ(self) -> None:
        """Same user in both channels must produce different scope_keys."""
        tg_identity = SessionIdentity(
            session_id="", channel_type="telegram", peer_id="42"
        )
        web_identity = SessionIdentity(session_id="main", channel_type="dm")

        tg_scope = resolve_scope_key(tg_identity, dm_scope="per-channel-peer")
        web_scope = resolve_scope_key(web_identity, dm_scope="main")

        assert tg_scope != web_scope
        assert tg_scope == "telegram:peer:42"
        assert web_scope == "main"


# ---------------------------------------------------------------------------
# 2. session isolation
# ---------------------------------------------------------------------------


class TestSessionIsolation:
    """Same Telegram user → same session; different users → different sessions."""

    def test_same_user_same_session(self) -> None:
        """Two messages from the same Telegram user resolve to the same session_id."""
        identity_a = SessionIdentity(
            session_id="", channel_type="telegram", peer_id="100"
        )
        identity_b = SessionIdentity(
            session_id="", channel_type="telegram", peer_id="100"
        )
        assert (
            resolve_session_key(identity_a, dm_scope="per-channel-peer")
            == resolve_session_key(identity_b, dm_scope="per-channel-peer")
        )

    def test_different_users_different_sessions(self) -> None:
        """Two different Telegram users resolve to different session_ids."""
        identity_a = SessionIdentity(
            session_id="", channel_type="telegram", peer_id="100"
        )
        identity_b = SessionIdentity(
            session_id="", channel_type="telegram", peer_id="200"
        )
        assert (
            resolve_session_key(identity_a, dm_scope="per-channel-peer")
            != resolve_session_key(identity_b, dm_scope="per-channel-peer")
        )

    def test_telegram_session_key_format(self) -> None:
        """Telegram DM session_id follows scope_key format."""
        identity = SessionIdentity(
            session_id="", channel_type="telegram", peer_id="555"
        )
        session_key = resolve_session_key(identity, dm_scope="per-channel-peer")
        assert session_key == "telegram:peer:555"


# ---------------------------------------------------------------------------
# 3. memory scope isolation
# ---------------------------------------------------------------------------


class TestMemoryScopeIsolation:
    """Telegram flush scope ≠ WebChat scope; memory written in one is
    not recallable in the other because scope_keys differ."""

    @pytest.mark.asyncio
    async def test_telegram_flush_scope_differs_from_webchat(
        self, tmp_path: Path,
    ) -> None:
        """Flush written with Telegram scope_key vs main scope_key → different scopes."""
        settings = MemorySettings(
            workspace_path=Path("workspace"),
            max_daily_note_bytes=32_768,
            daily_notes_load_days=2,
            daily_notes_max_tokens=4000,
            flush_min_confidence=0.5,
        )
        loop = _make_agent_loop(tmp_path, memory_settings=settings)

        tg_candidate = MemoryFlushCandidate(
            source_session_id="telegram:peer:42",
            candidate_text="Telegram memory fact",
            confidence=0.9,
        )
        web_candidate = MemoryFlushCandidate(
            source_session_id="main",
            candidate_text="WebChat memory fact",
            confidence=0.9,
        )

        await loop._persist_flush_candidates(
            [tg_candidate], "telegram:peer:42", scope_key="telegram:peer:42",
        )
        await loop._persist_flush_candidates(
            [web_candidate], "main", scope_key="main",
        )

        from datetime import date

        today = date.today()
        path = tmp_path / "memory" / f"{today.isoformat()}.md"
        content = path.read_text(encoding="utf-8")

        # Both are persisted, but with different scope tags
        assert "scope: telegram:peer:42" in content
        assert "scope: main" in content
        assert "Telegram memory fact" in content
        assert "WebChat memory fact" in content


# ---------------------------------------------------------------------------
# 4. tool mode / risk gating consistency
# ---------------------------------------------------------------------------


class TestToolModeConsistency:
    """Both channels use the same tool mode and risk gating — no channel-specific override."""

    @pytest.mark.asyncio
    async def test_telegram_dispatch_uses_same_mode_path(self) -> None:
        """dispatch_chat does not apply channel-specific mode filtering;
        tool mode comes from session_manager.get_mode, which is channel-agnostic."""
        registry, mgr, gate, entry = _make_dispatch_deps()

        # Spy on handle_message to capture call args
        call_kwargs_list: list[dict] = []

        async def spy(*args, **kwargs):
            call_kwargs_list.append(kwargs)
            return
            yield  # pragma: no cover

        entry.agent_loop.handle_message = spy

        # Telegram dispatch
        tg_identity = SessionIdentity(
            session_id="telegram:peer:42",
            channel_type="telegram",
            peer_id="42",
        )
        async for _ in dispatch_chat(
            registry=registry,
            session_manager=mgr,
            budget_gate=gate,
            session_id="telegram:peer:42",
            content="hello",
            identity=tg_identity,
            dm_scope="per-channel-peer",
        ):
            pass

        # WebChat dispatch (no identity, default scope)
        async for _ in dispatch_chat(
            registry=registry,
            session_manager=mgr,
            budget_gate=gate,
            session_id="main",
            content="hello",
        ):
            pass

        assert len(call_kwargs_list) == 2
        # Both go through the same handle_message path — no channel-specific mode override
        # Telegram call has identity + dm_scope
        assert call_kwargs_list[0]["identity"] is tg_identity
        assert call_kwargs_list[0]["dm_scope"] == "per-channel-peer"
        # WebChat call has identity=None, dm_scope=None
        assert call_kwargs_list[1]["identity"] is None
        assert call_kwargs_list[1]["dm_scope"] is None

    @pytest.mark.asyncio
    async def test_agent_loop_mode_independent_of_channel(self, tmp_path: Path) -> None:
        """AgentLoop.handle_message calls get_mode with session_id, not channel info."""
        loop = _make_agent_loop(tmp_path)

        identity = SessionIdentity(
            session_id="telegram:peer:42",
            channel_type="telegram",
            peer_id="42",
        )

        with patch("src.agent.agent.resolve_scope_key", return_value="telegram:peer:42"):
            try:
                async for _ in loop.handle_message(
                    "telegram:peer:42", "hello",
                    identity=identity,
                    dm_scope="per-channel-peer",
                ):
                    pass
            except Exception:
                pass  # expected: mock model_client can't stream

        # get_mode was called with session_id — no channel-specific logic
        loop._session_manager.get_mode.assert_called_once_with("telegram:peer:42")


# ---------------------------------------------------------------------------
# 5. flush persist scope consistency
# ---------------------------------------------------------------------------


class TestFlushPersistScopeConsistency:
    """Telegram session compaction flush scope_key = recall scope_key."""

    @pytest.mark.asyncio
    async def test_flush_scope_matches_recall_scope(self, tmp_path: Path) -> None:
        """scope_key used in flush is the same value that would be used for recall."""
        settings = MemorySettings(
            workspace_path=Path("workspace"),
            max_daily_note_bytes=32_768,
            daily_notes_load_days=2,
            daily_notes_max_tokens=4000,
            flush_min_confidence=0.5,
        )
        loop = _make_agent_loop(tmp_path, memory_settings=settings)

        # The scope_key that Telegram adapter would compute
        tg_identity = SessionIdentity(
            session_id="telegram:peer:99",
            channel_type="telegram",
            peer_id="99",
        )
        scope_key = resolve_scope_key(tg_identity, dm_scope="per-channel-peer")

        candidates = [
            MemoryFlushCandidate(
                source_session_id="telegram:peer:99",
                candidate_text="Important fact from Telegram",
                confidence=0.9,
            ),
        ]

        await loop._persist_flush_candidates(
            candidates, "telegram:peer:99", scope_key=scope_key,
        )

        from datetime import date

        today = date.today()
        path = tmp_path / "memory" / f"{today.isoformat()}.md"
        content = path.read_text(encoding="utf-8")

        # The flushed scope_key matches what resolve_scope_key would return for recall
        assert f"scope: {scope_key}" in content
        assert scope_key == "telegram:peer:99"

    @pytest.mark.asyncio
    async def test_dispatch_passes_consistent_identity_and_scope(self) -> None:
        """dispatch_chat passes the same identity/dm_scope to handle_message that
        the adapter computed for session resolution, ensuring flush and recall
        scope_keys are co-sourced."""
        registry, mgr, gate, entry = _make_dispatch_deps()

        call_kwargs_list: list[dict] = []

        async def spy(*args, **kwargs):
            call_kwargs_list.append(kwargs)
            return
            yield  # pragma: no cover

        entry.agent_loop.handle_message = spy

        # Simulate what TelegramAdapter._handle_dm does
        peer_id = "77"
        identity = SessionIdentity(
            session_id="", channel_type="telegram", peer_id=peer_id,
        )
        dm_scope = "per-channel-peer"
        session_id = resolve_session_key(identity, dm_scope)
        identity = SessionIdentity(
            session_id=session_id, channel_type="telegram", peer_id=peer_id,
        )

        async for _ in dispatch_chat(
            registry=registry,
            session_manager=mgr,
            budget_gate=gate,
            session_id=session_id,
            content="test",
            identity=identity,
            dm_scope=dm_scope,
        ):
            pass

        kwargs = call_kwargs_list[0]
        # session_id passed to handle_message matches what scope resolver returns
        assert kwargs["session_id"] == "telegram:peer:77"
        # identity and dm_scope are the same values used for session resolution
        assert kwargs["identity"].channel_type == "telegram"
        assert kwargs["identity"].peer_id == "77"
        assert kwargs["dm_scope"] == "per-channel-peer"
        # Verify scope_key derivable from these equals session_id
        derived_scope = resolve_scope_key(kwargs["identity"], dm_scope=kwargs["dm_scope"])
        assert derived_scope == kwargs["session_id"]
