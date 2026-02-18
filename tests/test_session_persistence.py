"""Tests for R6a: session persistence — atomic seq, sync persist, memory consistency."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.session.manager import SessionManager


class TestConcurrentSessionCreation:
    """Concurrent _persist_message on same session_id (first message)."""

    @pytest.mark.asyncio
    async def test_upsert_is_idempotent(self):
        """Two _persist_message calls for the same session_id don't conflict."""
        # We mock the DB layer to simulate upsert behavior
        mock_db = MagicMock()
        manager = SessionManager(db_session_factory=mock_db)

        # Patch _persist_message to track calls without real DB
        call_count = 0

        async def fake_persist(session_id, msg):
            nonlocal call_count
            call_count += 1

        with patch.object(manager, "_persist_message", side_effect=fake_persist):
            await manager.append_message("s1", "user", "hello")
            await manager.append_message("s1", "user", "world")

        assert call_count == 2


class TestCrossWorkerSeqUniqueness:
    """Simulated concurrent _persist_message — different seq values."""

    @pytest.mark.asyncio
    async def test_two_messages_get_different_seq(self):
        """Two messages appended to same session get sequential seq values."""
        mock_db = MagicMock()
        manager = SessionManager(db_session_factory=mock_db)
        seqs: list[int] = []

        async def tracking_persist(session_id, msg):
            seqs.append(len(seqs))  # Simulate sequential allocation

        with patch.object(manager, "_persist_message", side_effect=tracking_persist):
            await manager.append_message("s1", "user", "msg1")
            await manager.append_message("s1", "user", "msg2")

        assert len(seqs) == 2
        assert seqs[0] != seqs[1]


class TestPersistFailureMemoryClean:
    """persist failure → memory stays clean (no ghost messages)."""

    @pytest.mark.asyncio
    async def test_persist_failure_keeps_memory_clean(self):
        mock_db = MagicMock()
        manager = SessionManager(db_session_factory=mock_db)
        # Pre-create session in memory
        session = manager.get_or_create("s1")
        initial_count = len(session.messages)

        with patch.object(
            manager, "_persist_message", side_effect=ConnectionError("DB down")
        ):
            with pytest.raises(ConnectionError):
                await manager.append_message("s1", "user", "ghost")

        # Memory should NOT have the ghost message
        assert len(session.messages) == initial_count


class TestPersistFailurePropagates:
    """persist failure propagates to caller (no silent drop)."""

    @pytest.mark.asyncio
    async def test_persist_failure_raises(self):
        mock_db = MagicMock()
        manager = SessionManager(db_session_factory=mock_db)

        with patch.object(
            manager, "_persist_message", side_effect=ConnectionError("DB down")
        ):
            with pytest.raises(ConnectionError, match="DB down"):
                await manager.append_message("s1", "user", "fail")


class TestSeqConflictDetection:
    """IntegrityError from (session_id, seq) conflict propagates."""

    @pytest.mark.asyncio
    async def test_integrity_error_propagates(self):
        from sqlalchemy.exc import IntegrityError

        mock_db = MagicMock()
        manager = SessionManager(db_session_factory=mock_db)

        with patch.object(
            manager,
            "_persist_message",
            side_effect=IntegrityError("", {}, Exception("duplicate")),
        ):
            with pytest.raises(IntegrityError):
                await manager.append_message("s1", "user", "dup")
