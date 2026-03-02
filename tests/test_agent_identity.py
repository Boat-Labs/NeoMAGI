"""Tests for AgentLoop identity + dm_scope propagation (P1).

Covers:
- Telegram identity with per-channel-peer → correct scope_key
- Backward compatibility: identity=None, dm_scope=None → existing behavior
- Flush scope: scope_key passed through to _persist_flush_candidates
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.agent import AgentLoop
from src.config.settings import MemorySettings, SessionSettings
from src.session.scope_resolver import SessionIdentity


def _make_agent_loop(
    tmp_path: Path,
    *,
    session_settings: SessionSettings | None = None,
    memory_settings: MemorySettings | None = None,
) -> AgentLoop:
    model_client = MagicMock()
    session_manager = MagicMock()
    # Provide minimal async stubs
    session_manager.append_message = AsyncMock(
        return_value=MagicMock(seq=1)
    )
    session_manager.get_mode = AsyncMock(return_value="chat_safe")
    session_manager.get_compaction_state = AsyncMock(return_value=None)
    session_manager.get_history_with_seq = MagicMock(return_value=[])

    return AgentLoop(
        model_client=model_client,
        session_manager=session_manager,
        workspace_dir=tmp_path,
        session_settings=session_settings,
        memory_settings=memory_settings,
    )


class TestHandleMessageIdentity:
    @pytest.mark.asyncio
    async def test_telegram_identity_per_channel_peer(self, tmp_path: Path) -> None:
        """When identity + dm_scope provided, scope_key uses them."""
        loop = _make_agent_loop(tmp_path)

        identity = SessionIdentity(
            session_id="tg-123", channel_type="telegram", peer_id="456"
        )

        # We patch resolve_scope_key to capture what it receives
        with patch("src.agent.agent.resolve_scope_key", return_value="telegram:peer:456") as mock_rsk:
            # The LLM call will fail since model_client is a mock — that's OK,
            # we only need to verify scope resolution params.
            try:
                async for _ in loop.handle_message(
                    "tg-123",
                    "hello",
                    identity=identity,
                    dm_scope="per-channel-peer",
                ):
                    pass
            except Exception:
                pass  # expected: mock model_client can't stream

            mock_rsk.assert_called_once_with(identity, dm_scope="per-channel-peer")

    @pytest.mark.asyncio
    async def test_backward_compat_no_identity(self, tmp_path: Path) -> None:
        """When identity=None, dm_scope=None → uses defaults."""
        settings = SessionSettings()
        loop = _make_agent_loop(tmp_path, session_settings=settings)

        with patch("src.agent.agent.resolve_scope_key", return_value="main") as mock_rsk:
            try:
                async for _ in loop.handle_message("main", "hello"):
                    pass
            except Exception:
                pass

            call_args = mock_rsk.call_args
            # Should construct SessionIdentity(session_id="main", channel_type="dm")
            identity_arg = call_args[0][0]
            assert identity_arg.session_id == "main"
            assert identity_arg.channel_type == "dm"
            assert call_args[1]["dm_scope"] == "main"

    @pytest.mark.asyncio
    async def test_dm_scope_override_without_identity(self, tmp_path: Path) -> None:
        """dm_scope can be overridden even without identity."""
        loop = _make_agent_loop(tmp_path)

        with patch("src.agent.agent.resolve_scope_key", return_value="main") as mock_rsk:
            try:
                async for _ in loop.handle_message(
                    "s1", "hello", dm_scope="main"
                ):
                    pass
            except Exception:
                pass

            call_args = mock_rsk.call_args
            assert call_args[1]["dm_scope"] == "main"


class TestFlushScopePassthrough:
    @pytest.mark.asyncio
    async def test_persist_flush_candidates_receives_scope_key(self, tmp_path: Path) -> None:
        """_persist_flush_candidates receives scope_key from caller."""
        from src.agent.memory_flush import MemoryFlushCandidate

        settings = MemorySettings(workspace_path=Path("workspace"))
        loop = _make_agent_loop(tmp_path, memory_settings=settings)

        candidates = [
            MemoryFlushCandidate(
                source_session_id="s1",
                candidate_text="test fact",
                confidence=0.9,
            ),
        ]

        await loop._persist_flush_candidates(
            candidates, "s1", scope_key="telegram:peer:789"
        )

        from datetime import date

        today = date.today()
        path = tmp_path / "memory" / f"{today.isoformat()}.md"
        content = path.read_text(encoding="utf-8")
        assert "scope: telegram:peer:789" in content
