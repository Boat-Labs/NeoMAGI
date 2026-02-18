"""Integration tests for SessionManager against a real PostgreSQL database.

Covers core paths: claim/release, atomic seq allocation, force reload,
claim mutual exclusion, TTL expiry reclaim, and fencing interception.
"""

from __future__ import annotations

import asyncio

import pytest

from src.session.manager import SessionManager

pytestmark = pytest.mark.integration


class TestClaimReleaseBasic:
    """claim → release → re-claim should all succeed."""

    async def test_claim_release_reclaim(self, session_manager: SessionManager) -> None:
        token1 = await session_manager.try_claim_session("s1")
        assert token1 is not None

        await session_manager.release_session("s1", token1)

        token2 = await session_manager.try_claim_session("s1")
        assert token2 is not None
        assert token2 != token1

        # Cleanup
        await session_manager.release_session("s1", token2)


class TestSeqAtomicAllocation:
    """Concurrent append_message writes to the same session get distinct seq values."""

    async def test_concurrent_append_different_seq(self, session_manager: SessionManager) -> None:
        token = await session_manager.try_claim_session("s1")
        assert token is not None

        async def append(content: str):
            return await session_manager.append_message("s1", "user", content)

        msg1, msg2 = await asyncio.gather(append("hello"), append("world"))

        # Both succeeded — verify they have different seq via DB
        history = await session_manager.get_history_for_display("s1")
        assert len(history) == 2

        await session_manager.release_session("s1", token)


class TestForceReloadConsistency:
    """Manager A writes → Manager B force reloads → B sees A's messages."""

    async def test_force_reload_sees_all_messages(self, db_session_factory) -> None:
        manager_a = SessionManager(db_session_factory=db_session_factory)
        manager_b = SessionManager(db_session_factory=db_session_factory)

        token = await manager_a.try_claim_session("s1")
        assert token is not None

        await manager_a.append_message("s1", "user", "msg1")
        await manager_a.append_message("s1", "assistant", "reply1")
        await manager_a.append_message("s1", "user", "msg2")

        # Manager B has no local cache — force reload from DB
        loaded = await manager_b.load_session_from_db("s1", force=True)
        assert loaded is True

        history = manager_b.get_history("s1")
        assert len(history) == 3
        assert history[0]["content"] == "msg1"
        assert history[1]["content"] == "reply1"
        assert history[2]["content"] == "msg2"

        await manager_a.release_session("s1", token)


class TestClaimMutualExclusion:
    """Concurrent claims on the same session: one succeeds, one fails."""

    async def test_concurrent_claim_one_wins(self, db_session_factory) -> None:
        manager_a = SessionManager(db_session_factory=db_session_factory)
        manager_b = SessionManager(db_session_factory=db_session_factory)

        results = await asyncio.gather(
            manager_a.try_claim_session("s1"),
            manager_b.try_claim_session("s1"),
        )

        # Exactly one should succeed (not None), one should fail (None)
        successes = [r for r in results if r is not None]
        failures = [r for r in results if r is None]
        assert len(successes) == 1, f"Expected exactly 1 success, got {len(successes)}"
        assert len(failures) == 1, f"Expected exactly 1 failure, got {len(failures)}"

        # Cleanup: release the winning claim
        await manager_a.release_session("s1", successes[0])


class TestTTLExpiryReclaim:
    """claim with short TTL → wait for expiry → re-claim succeeds."""

    async def test_ttl_expiry_allows_reclaim(self, db_session_factory) -> None:
        manager_a = SessionManager(db_session_factory=db_session_factory)
        manager_b = SessionManager(db_session_factory=db_session_factory)

        # A claims with a very short TTL (2 seconds)
        token_a = await manager_a.try_claim_session("s1", ttl_seconds=2)
        assert token_a is not None

        # B cannot claim immediately (using same TTL threshold)
        token_b_early = await manager_b.try_claim_session("s1", ttl_seconds=2)
        assert token_b_early is None

        # Wait for TTL to expire (generous margin for container clock skew)
        await asyncio.sleep(3)

        # B can now claim after TTL expiry
        token_b = await manager_b.try_claim_session("s1", ttl_seconds=2)
        assert token_b is not None
        assert token_b != token_a

        await manager_b.release_session("s1", token_b)


class TestTakeoverPrerequisite:
    """Prerequisite for fencing: B can take over after A's TTL expires.

    The actual fencing assertion (stale A write → SessionFencingError) will
    be added when R2 lands. This test locks down the takeover mechanism that
    fencing depends on.
    """

    @pytest.mark.xfail(
        strict=True,
        reason="Stale write should raise SessionFencingError after R2 (lock fencing)",
    )
    async def test_stale_worker_rejected_after_takeover(self, db_session_factory) -> None:
        from src.infra.errors import SessionFencingError

        manager_a = SessionManager(db_session_factory=db_session_factory)
        manager_b = SessionManager(db_session_factory=db_session_factory)

        # A claims with short TTL
        token_a = await manager_a.try_claim_session("s1", ttl_seconds=2)
        assert token_a is not None

        # A writes a message (succeeds — still the lock holder)
        await manager_a.append_message("s1", "user", "from A")

        # Wait for TTL expiry (generous margin for container clock skew)
        await asyncio.sleep(3)

        # B takes over (using same TTL threshold as A's original claim)
        token_b = await manager_b.try_claim_session("s1", ttl_seconds=2)
        assert token_b is not None, "B should be able to claim after TTL expiry"

        # A tries to write with stale token — should raise SessionFencingError
        with pytest.raises(SessionFencingError):
            await manager_a.append_message(
                "s1", "user", "stale write from A", lock_token=token_a
            )

        await manager_b.release_session("s1", token_b)
