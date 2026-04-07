"""Tests for procedure view integration in PromptBuilder."""

from __future__ import annotations

from pathlib import Path

from src.agent.prompt_builder import PromptBuilder
from src.procedures.types import ProcedureView
from src.tools.base import ToolMode


class TestPromptBuilderProcedureView:
    def _builder(self) -> PromptBuilder:
        return PromptBuilder(Path("/tmp/test-workspace"))

    def test_no_procedure_view_unchanged(self) -> None:
        builder = self._builder()
        prompt = builder.build("s1", ToolMode.coding)
        assert "Active Procedure" not in prompt

    def test_with_procedure_view(self) -> None:
        builder = self._builder()
        view = ProcedureView(
            id="test.proc",
            version=2,
            summary="A test procedure",
            state="draft",
            revision=3,
            allowed_actions=("submit", "cancel"),
            soft_policies=("be_careful",),
        )
        prompt = builder.build("s1", ToolMode.coding, procedure_view=view)
        assert "Active Procedure" in prompt
        assert "test.proc" in prompt
        assert "v2" in prompt
        assert "draft" in prompt
        assert "submit" in prompt
        assert "cancel" in prompt
        assert "be_careful" in prompt

    def test_terminal_state_shows_none(self) -> None:
        builder = self._builder()
        view = ProcedureView(
            id="test.proc",
            version=1,
            summary="Done procedure",
            state="done",
            revision=5,
            allowed_actions=(),
        )
        prompt = builder.build("s1", ToolMode.coding, procedure_view=view)
        assert "terminal state" in prompt
