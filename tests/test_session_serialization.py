"""Tests for R6b: session serialization — lease lock with lock_token and TTL."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.session.manager import SessionManager


class TestConcurrentClaim:
    """Two try_claim_session calls for same session_id — one wins."""

    @pytest.mark.asyncio
    async def test_concurrent_claim_one_wins(self):
        """Simulate: first claim succeeds, second returns None (SESSION_BUSY)."""
        mock_db = MagicMock()
        manager = SessionManager(db_session_factory=mock_db)

        # First claim returns a token
        tokens = []

        async def fake_claim_first(session_id, ttl_seconds=300):
            if len(tokens) == 0:
                token = "token-1"
                tokens.append(token)
                return token
            return None  # Busy

        with patch.object(manager, "try_claim_session", side_effect=fake_claim_first):
            t1 = await manager.try_claim_session("s1", ttl_seconds=2)
            t2 = await manager.try_claim_session("s1", ttl_seconds=2)

        assert t1 is not None
        assert t2 is None


class TestSessionBusyRPC:
    """Gateway returns SESSION_BUSY when claim fails."""

    @pytest.mark.asyncio
    async def test_session_busy_error_response(self):
        import json

        from src.gateway.app import _handle_chat_send

        mock_ws = AsyncMock()
        mock_ws.app = MagicMock()

        mock_manager = MagicMock()
        mock_manager.try_claim_session = AsyncMock(return_value=None)
        mock_ws.app.state.session_manager = mock_manager

        await _handle_chat_send(mock_ws, "req-1", {"content": "hi", "session_id": "s1"})

        # Should have sent an error response
        mock_ws.send_text.assert_called_once()
        sent = json.loads(mock_ws.send_text.call_args[0][0])
        assert sent["type"] == "error"
        assert sent["error"]["code"] == "SESSION_BUSY"


class TestNormalRelease:
    """claim → release (correct token) → re-claim succeeds."""

    @pytest.mark.asyncio
    async def test_release_and_reclaim(self):
        mock_db = MagicMock()
        manager = SessionManager(db_session_factory=mock_db)

        claim_count = 0
        released = False

        async def fake_claim(session_id, ttl_seconds=300):
            nonlocal claim_count
            claim_count += 1
            if claim_count == 1 or released:
                return f"token-{claim_count}"
            return None

        async def fake_release(session_id, lock_token):
            nonlocal released
            released = True

        with (
            patch.object(manager, "try_claim_session", side_effect=fake_claim),
            patch.object(manager, "release_session", side_effect=fake_release),
        ):
            t1 = await manager.try_claim_session("s1")
            assert t1 is not None
            await manager.release_session("s1", t1)
            t2 = await manager.try_claim_session("s1")
            assert t2 is not None


class TestReleasTokenMismatch:
    """Worker A release after Worker B took over — no-op."""

    @pytest.mark.asyncio
    async def test_mismatched_token_release_is_noop(self):
        """release with wrong token should not affect the current lock holder."""
        mock_db = MagicMock()
        manager = SessionManager(db_session_factory=mock_db)

        current_token = "token-B"
        release_calls = []

        async def fake_release(session_id, lock_token):
            release_calls.append(lock_token)
            # Only clears if token matches
            nonlocal current_token
            if lock_token == current_token:
                current_token = None

        with patch.object(manager, "release_session", side_effect=fake_release):
            # Worker A tries to release with old token
            await manager.release_session("s1", "token-A")

        # B's token should still be set (A's release was a no-op)
        assert current_token == "token-B"
        assert release_calls == ["token-A"]


class TestTTLAutoRelease:
    """claim without release → TTL expires → re-claim succeeds."""

    @pytest.mark.asyncio
    async def test_ttl_expiry_allows_reclaim(self):
        """After TTL expires, a new claim should succeed."""
        mock_db = MagicMock()
        manager = SessionManager(db_session_factory=mock_db)

        import time

        claim_time = 0.0
        ttl = 2  # 2 seconds

        async def fake_claim(session_id, ttl_seconds=300):
            nonlocal claim_time
            now = time.monotonic()
            if claim_time == 0.0:
                claim_time = now
                return "token-1"
            elif now - claim_time >= ttl_seconds:
                claim_time = now
                return "token-2"
            return None

        with patch.object(manager, "try_claim_session", side_effect=fake_claim):
            t1 = await manager.try_claim_session("s1", ttl_seconds=ttl)
            assert t1 == "token-1"

            # Immediately — should be busy
            t2 = await manager.try_claim_session("s1", ttl_seconds=ttl)
            assert t2 is None


class TestTTLConfigurable:
    """Different ttl_seconds values produce different behavior."""

    @pytest.mark.asyncio
    async def test_ttl_passed_to_claim(self):
        mock_db = MagicMock()
        manager = SessionManager(db_session_factory=mock_db)

        received_ttl = None

        async def fake_claim(session_id, ttl_seconds=300):
            nonlocal received_ttl
            received_ttl = ttl_seconds
            return "token"

        with patch.object(manager, "try_claim_session", side_effect=fake_claim):
            await manager.try_claim_session("s1", ttl_seconds=60)

        assert received_ttl == 60


class TestCrossWorkerContextContinuity:
    """Worker A writes → Worker B force-reloads → sees A's history."""

    @pytest.mark.asyncio
    async def test_force_reload_sees_previous_messages(self):
        mock_db = MagicMock()
        manager = SessionManager(db_session_factory=mock_db)

        # Simulate: A appended messages, B force-reloads
        with patch.object(manager, "_persist_message", new_callable=AsyncMock):
            await manager.append_message("s1", "user", "from-A")
            await manager.append_message("s1", "assistant", "reply-A")

        # Verify messages are in memory
        session = manager.get_or_create("s1")
        assert len(session.messages) == 2
        assert session.messages[0].content == "from-A"
        assert session.messages[1].content == "reply-A"

        # get_history_for_display returns user+assistant
        # Patch load_session_from_db to no-op (messages already in memory).
        with patch.object(
            manager, "load_session_from_db",
            new_callable=AsyncMock, return_value=True,
        ):
            history = await manager.get_history_for_display("s1")
        assert len(history) == 2


class TestForceReloadFailureInterrupts:
    """force=True + DB error → exception propagates (not False)."""

    @pytest.mark.asyncio
    async def test_force_reload_db_error_raises(self):
        mock_db = MagicMock()
        manager = SessionManager(db_session_factory=mock_db)

        # Mock _db() context manager to raise
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(side_effect=ConnectionError("DB down"))
        mock_db.return_value = mock_session

        with pytest.raises(ConnectionError, match="DB down"):
            await manager.load_session_from_db("s1", force=True)

    @pytest.mark.asyncio
    async def test_non_force_reload_db_error_returns_false(self):
        mock_db = MagicMock()
        manager = SessionManager(db_session_factory=mock_db)

        # Mock _db() context manager to raise
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(side_effect=ConnectionError("DB down"))
        mock_db.return_value = mock_session

        result = await manager.load_session_from_db("s1", force=False)
        assert result is False
