"""Tests for memory contracts (ResolvedFlushCandidate)."""

from __future__ import annotations

import pytest

from src.memory.contracts import ResolvedFlushCandidate


class TestResolvedFlushCandidate:
    def test_create(self) -> None:
        c = ResolvedFlushCandidate(
            candidate_text="test",
            scope_key="main",
            source_session_id="s1",
        )
        assert c.candidate_text == "test"
        assert c.scope_key == "main"
        assert c.source_session_id == "s1"
        assert c.confidence == 0.0
        assert c.constraint_tags == ()

    def test_custom_fields(self) -> None:
        c = ResolvedFlushCandidate(
            candidate_text="note",
            scope_key="main",
            source_session_id="s1",
            confidence=0.9,
            constraint_tags=("user_preference", "safety_boundary"),
        )
        assert c.confidence == 0.9
        assert len(c.constraint_tags) == 2

    def test_frozen(self) -> None:
        c = ResolvedFlushCandidate(
            candidate_text="test",
            scope_key="main",
            source_session_id="s1",
        )
        with pytest.raises(AttributeError):
            c.candidate_text = "changed"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = ResolvedFlushCandidate(
            candidate_text="same", scope_key="main", source_session_id="s1"
        )
        b = ResolvedFlushCandidate(
            candidate_text="same", scope_key="main", source_session_id="s1"
        )
        assert a == b

    def test_inequality(self) -> None:
        a = ResolvedFlushCandidate(
            candidate_text="a", scope_key="main", source_session_id="s1"
        )
        b = ResolvedFlushCandidate(
            candidate_text="b", scope_key="main", source_session_id="s1"
        )
        assert a != b
