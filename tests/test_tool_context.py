"""Tests for ToolContext (Phase 0)."""

from __future__ import annotations

import pytest

from src.tools.context import ToolContext


class TestToolContext:
    def test_create_with_defaults(self) -> None:
        ctx = ToolContext()
        assert ctx.scope_key == "main"
        assert ctx.session_id == "main"

    def test_create_with_custom_values(self) -> None:
        ctx = ToolContext(scope_key="peer:alice", session_id="sess-123")
        assert ctx.scope_key == "peer:alice"
        assert ctx.session_id == "sess-123"

    def test_frozen_immutable(self) -> None:
        ctx = ToolContext()
        with pytest.raises(AttributeError):
            ctx.scope_key = "other"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = ToolContext(scope_key="main", session_id="s1")
        b = ToolContext(scope_key="main", session_id="s1")
        assert a == b

    def test_inequality(self) -> None:
        a = ToolContext(scope_key="main", session_id="s1")
        b = ToolContext(scope_key="main", session_id="s2")
        assert a != b
