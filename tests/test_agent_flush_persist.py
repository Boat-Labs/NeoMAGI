"""Tests for AgentLoop flush candidate persistence (Phase 1).

Covers:
- compaction → auto-persist flush candidates
- no candidates → skip
- persist failure → does not crash main flow
- scope_key resolved from candidate.source_session_id
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.agent import AgentLoop
from src.agent.memory_flush import MemoryFlushCandidate
from src.config.settings import MemorySettings


def _make_settings() -> MemorySettings:
    return MemorySettings(
        workspace_path=Path("workspace"),
        max_daily_note_bytes=32_768,
        daily_notes_load_days=2,
        daily_notes_max_tokens=4000,
        flush_min_confidence=0.5,
    )


def _make_agent_loop(tmp_path: Path, *, memory_settings: MemorySettings | None = None) -> AgentLoop:
    model_client = MagicMock()
    session_manager = MagicMock()
    return AgentLoop(
        model_client=model_client,
        session_manager=session_manager,
        workspace_dir=tmp_path,
        memory_settings=memory_settings,
    )


class TestPersistFlushCandidates:
    @pytest.mark.asyncio
    async def test_persists_candidates_after_compaction(self, tmp_path: Path) -> None:
        settings = _make_settings()
        loop = _make_agent_loop(tmp_path, memory_settings=settings)

        candidates = [
            MemoryFlushCandidate(
                source_session_id="main",
                candidate_text="User prefers dark mode",
                confidence=0.9,
                constraint_tags=["user_preference"],
            ),
        ]

        await loop._persist_flush_candidates(candidates, "main")

        # Verify file was written
        from datetime import date

        today = date.today()
        path = tmp_path / "memory" / f"{today.isoformat()}.md"
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "User prefers dark mode" in content
        assert "scope: main" in content
        assert "source: compaction_flush" in content

    @pytest.mark.asyncio
    async def test_skips_when_no_writer(self, tmp_path: Path) -> None:
        """No MemoryWriter → persist not called (no crash)."""
        loop = _make_agent_loop(tmp_path, memory_settings=None)
        assert loop._memory_writer is None
        # _persist_flush_candidates won't be called if _memory_writer is None
        # since the guard is in _try_compact. But we test the method directly:
        # it should still not crash.
        await loop._persist_flush_candidates([], "main")

    @pytest.mark.asyncio
    async def test_empty_candidates_no_write(self, tmp_path: Path) -> None:
        settings = _make_settings()
        loop = _make_agent_loop(tmp_path, memory_settings=settings)

        await loop._persist_flush_candidates([], "main")

        # No file should be created
        from datetime import date

        today = date.today()
        path = tmp_path / "memory" / f"{today.isoformat()}.md"
        assert not path.exists()

    @pytest.mark.asyncio
    async def test_persist_failure_does_not_crash(self, tmp_path: Path) -> None:
        """MemoryWriter failure is logged but does not propagate."""
        settings = _make_settings()
        loop = _make_agent_loop(tmp_path, memory_settings=settings)

        # Make writer's append_daily_note raise
        loop._memory_writer.append_daily_note = AsyncMock(side_effect=OSError("disk full"))

        candidates = [
            MemoryFlushCandidate(
                source_session_id="main",
                candidate_text="test",
                confidence=0.9,
            ),
        ]

        # Should not raise
        await loop._persist_flush_candidates(candidates, "main")

    @pytest.mark.asyncio
    async def test_scope_key_from_candidate_session_id(self, tmp_path: Path) -> None:
        """scope_key is resolved from candidate.source_session_id, not current session_id."""
        settings = _make_settings()
        loop = _make_agent_loop(tmp_path, memory_settings=settings)

        candidates = [
            MemoryFlushCandidate(
                source_session_id="main",
                candidate_text="From main session",
                confidence=0.9,
            ),
        ]

        # Call with a different "current" session_id
        await loop._persist_flush_candidates(candidates, "different-session")

        from datetime import date

        today = date.today()
        path = tmp_path / "memory" / f"{today.isoformat()}.md"
        content = path.read_text(encoding="utf-8")
        # scope should be "main" (from candidate.source_session_id), not "different-session"
        assert "scope: main" in content

    @pytest.mark.asyncio
    async def test_filters_by_confidence(self, tmp_path: Path) -> None:
        settings = _make_settings()
        loop = _make_agent_loop(tmp_path, memory_settings=settings)

        candidates = [
            MemoryFlushCandidate(
                source_session_id="main",
                candidate_text="Low confidence",
                confidence=0.2,
            ),
            MemoryFlushCandidate(
                source_session_id="main",
                candidate_text="High confidence",
                confidence=0.8,
            ),
        ]

        await loop._persist_flush_candidates(candidates, "main")

        from datetime import date

        today = date.today()
        path = tmp_path / "memory" / f"{today.isoformat()}.md"
        content = path.read_text(encoding="utf-8")
        assert "High confidence" in content
        assert "Low confidence" not in content
