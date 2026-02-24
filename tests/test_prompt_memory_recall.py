"""Tests for PromptBuilder._layer_memory_recall and extract_recall_query."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from src.agent.prompt_builder import PromptBuilder
from src.config.settings import MemorySettings
from src.memory.searcher import MemorySearchResult
from src.tools.base import ToolMode


def _make_result(
    content: str,
    *,
    score: float = 1.5,
    source_type: str = "daily_note",
    entry_id: int = 1,
) -> MemorySearchResult:
    return MemorySearchResult(
        entry_id=entry_id,
        scope_key="main",
        source_type=source_type,
        source_path=None,
        title="",
        content=content,
        score=score,
        tags=[],
        created_at=datetime.now(UTC),
    )


def _make_builder(
    workspace: Path,
    *,
    memory_recall_max_tokens: int = 2000,
) -> PromptBuilder:
    settings = MemorySettings(
        workspace_path=workspace,
        max_daily_note_bytes=32_768,
        daily_notes_load_days=2,
        daily_notes_max_tokens=4000,
        flush_min_confidence=0.5,
        memory_recall_max_tokens=memory_recall_max_tokens,
    )
    return PromptBuilder(workspace, memory_settings=settings)


class TestLayerMemoryRecall:
    def test_no_results_returns_empty(self, tmp_path: Path) -> None:
        builder = _make_builder(tmp_path)
        result = builder._layer_memory_recall(recall_results=None)
        assert result == ""

    def test_empty_list_returns_empty(self, tmp_path: Path) -> None:
        builder = _make_builder(tmp_path)
        result = builder._layer_memory_recall(recall_results=[])
        assert result == ""

    def test_normal_injection(self, tmp_path: Path) -> None:
        results = [
            _make_result("User prefers dark mode"),
            _make_result("Project uses PostgreSQL 16", source_type="curated"),
        ]
        builder = _make_builder(tmp_path)
        result = builder._layer_memory_recall(recall_results=results)

        assert "[Recalled Memories]" in result
        assert "User prefers dark mode" in result
        assert "Project uses PostgreSQL 16" in result
        assert "daily_note" in result
        assert "curated" in result

    def test_token_truncation(self, tmp_path: Path) -> None:
        """Results exceeding max_tokens are truncated."""
        results = [
            _make_result(f"Entry {i}: " + "x" * 200, entry_id=i)
            for i in range(20)
        ]
        builder = _make_builder(tmp_path, memory_recall_max_tokens=100)
        result = builder._layer_memory_recall(recall_results=results)

        # Should not include all 20 entries (100 tokens * 4 chars = 400 char limit)
        assert "[Recalled Memories]" in result
        assert result.count("- (") < 20

    def test_content_per_entry_truncated(self, tmp_path: Path) -> None:
        """Individual entry content is truncated at 300 chars."""
        long_content = "A" * 500
        results = [_make_result(long_content)]
        builder = _make_builder(tmp_path)
        result = builder._layer_memory_recall(recall_results=results)

        # Content should be truncated (300 chars max per entry)
        assert len(result) < 500

    def test_recall_in_full_build(self, tmp_path: Path) -> None:
        """recall_results appear in full build() output."""
        results = [_make_result("dark mode preference")]
        builder = _make_builder(tmp_path)
        prompt = builder.build(
            "test-session",
            ToolMode.chat_safe,
            scope_key="main",
            recall_results=results,
        )
        assert "[Recalled Memories]" in prompt
        assert "dark mode preference" in prompt

    def test_build_without_recall_no_section(self, tmp_path: Path) -> None:
        """build() without recall_results has no [Recalled Memories] section."""
        builder = _make_builder(tmp_path)
        prompt = builder.build("test-session", ToolMode.chat_safe, scope_key="main")
        assert "[Recalled Memories]" not in prompt


class TestExtractRecallQuery:
    def test_none_returns_empty(self) -> None:
        assert PromptBuilder.extract_recall_query(None) == ""

    def test_empty_list_returns_empty(self) -> None:
        assert PromptBuilder.extract_recall_query([]) == ""

    def test_whitespace_only_returns_empty(self) -> None:
        assert PromptBuilder.extract_recall_query(["  ", ""]) == ""

    def test_normal_messages(self) -> None:
        messages = ["hello world", "what is dark mode"]
        result = PromptBuilder.extract_recall_query(messages)
        assert "hello world" in result
        assert "dark mode" in result

    def test_max_query_len(self) -> None:
        messages = ["a" * 300]
        result = PromptBuilder.extract_recall_query(messages, max_query_len=50)
        assert len(result) == 50

    def test_strips_whitespace(self) -> None:
        messages = ["  hello  ", "  world  "]
        result = PromptBuilder.extract_recall_query(messages)
        assert result == "hello world"
