"""Tests for PromptBuilder scope_key extension (Phase 0)."""

from __future__ import annotations

from pathlib import Path

from src.agent.prompt_builder import PromptBuilder
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
