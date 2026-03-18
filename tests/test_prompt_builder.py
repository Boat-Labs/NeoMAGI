"""Tests for PromptBuilder scope_key extension and skill layer (Phase 0 + P2-M1b)."""

from __future__ import annotations

from pathlib import Path

from src.agent.prompt_builder import PromptBuilder
from src.skills.types import ResolvedSkillView
from src.tools.base import ToolMode


class TestPromptBuilderScopeKey:
    def _make_builder(self, tmp_path: Path) -> PromptBuilder:
        return PromptBuilder(tmp_path)

    def test_build_accepts_scope_key(self, tmp_path: Path) -> None:
        builder = self._make_builder(tmp_path)
        result = builder.build("main", ToolMode.chat_safe, scope_key="main")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_build_default_scope_key_is_main(self, tmp_path: Path) -> None:
        builder = self._make_builder(tmp_path)
        result_default = builder.build("main", ToolMode.chat_safe)
        result_explicit = builder.build("main", ToolMode.chat_safe, scope_key="main")
        assert result_default == result_explicit

    def test_memory_md_loaded_for_main_scope(self, tmp_path: Path) -> None:
        (tmp_path / "MEMORY.md").write_text("# Memory\nSome memory content")
        builder = self._make_builder(tmp_path)
        result = builder.build("main", ToolMode.chat_safe, scope_key="main")
        assert "Some memory content" in result

    def test_memory_md_not_loaded_for_non_main_scope(self, tmp_path: Path) -> None:
        (tmp_path / "MEMORY.md").write_text("# Memory\nSome memory content")
        builder = self._make_builder(tmp_path)
        result = builder.build("main", ToolMode.chat_safe, scope_key="peer:alice")
        assert "Some memory content" not in result

    def test_build_accepts_recent_messages(self, tmp_path: Path) -> None:
        builder = self._make_builder(tmp_path)
        result = builder.build(
            "main",
            ToolMode.chat_safe,
            scope_key="main",
            recent_messages=["hello", "world"],
        )
        assert isinstance(result, str)


class TestPromptBuilderSkillLayer:
    """Tests for skill delta injection into the system prompt (P2-M1b)."""

    def _make_builder(self, tmp_path: Path) -> PromptBuilder:
        return PromptBuilder(tmp_path)

    def test_layer_skills_with_view(self, tmp_path: Path) -> None:
        """ResolvedSkillView with delta entries injects a Skill Experience section."""
        builder = self._make_builder(tmp_path)
        view = ResolvedSkillView(llm_delta=("Prefer concise output",))
        result = builder.build("s1", ToolMode.chat_safe, skill_view=view)
        assert "## Skill Experience" in result
        assert "Prefer concise output" in result

    def test_layer_skills_empty_view(self, tmp_path: Path) -> None:
        """Empty ResolvedSkillView (no delta) should not inject anything."""
        builder = self._make_builder(tmp_path)
        view = ResolvedSkillView()
        result = builder.build("s1", ToolMode.chat_safe, skill_view=view)
        assert "## Skill Experience" not in result

    def test_layer_skills_none(self, tmp_path: Path) -> None:
        """None skill_view should not inject anything (backward compat)."""
        builder = self._make_builder(tmp_path)
        result = builder.build("s1", ToolMode.chat_safe, skill_view=None)
        assert "## Skill Experience" not in result

    def test_skill_layer_position(self, tmp_path: Path) -> None:
        """Skill Experience section appears after Safety and before Workspace."""
        builder = self._make_builder(tmp_path)
        (tmp_path / "AGENTS.md").write_text("# Agent Rules")
        view = ResolvedSkillView(llm_delta=("Use markdown",))
        result = builder.build("s1", ToolMode.chat_safe, skill_view=view)
        safety_pos = result.find("## Safety")
        skill_pos = result.find("## Skill Experience")
        workspace_pos = result.find("## Project Context")
        assert safety_pos < skill_pos < workspace_pos
