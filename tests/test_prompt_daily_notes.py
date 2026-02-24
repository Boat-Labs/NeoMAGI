"""Tests for PromptBuilder._load_daily_notes and _filter_entries_by_scope."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from src.agent.prompt_builder import PromptBuilder
from src.config.settings import MemorySettings


def _make_builder(
    workspace: Path, *, daily_notes_load_days: int = 2, daily_notes_max_tokens: int = 4000
) -> PromptBuilder:
    settings = MemorySettings(
        workspace_path=workspace,
        max_daily_note_bytes=32_768,
        daily_notes_load_days=daily_notes_load_days,
        daily_notes_max_tokens=daily_notes_max_tokens,
        flush_min_confidence=0.5,
    )
    return PromptBuilder(workspace, memory_settings=settings)


def _write_daily_note(workspace: Path, target_date: date, content: str) -> Path:
    memory_dir = workspace / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    filepath = memory_dir / f"{target_date.isoformat()}.md"
    filepath.write_text(content, encoding="utf-8")
    return filepath


class TestLoadDailyNotes:
    def test_loads_today_and_yesterday(self, tmp_path: Path) -> None:
        today = date.today()
        yesterday = today - timedelta(days=1)
        _write_daily_note(tmp_path, today, "---\n[10:00] (source: user, scope: main)\nToday note")
        _write_daily_note(
            tmp_path, yesterday, "---\n[09:00] (source: user, scope: main)\nYesterday note"
        )

        builder = _make_builder(tmp_path)
        result = builder._load_daily_notes(scope_key="main")

        assert "Today note" in result
        assert "Yesterday note" in result
        assert f"=== {today.isoformat()} ===" in result
        assert f"=== {yesterday.isoformat()} ===" in result

    def test_only_today_exists(self, tmp_path: Path) -> None:
        today = date.today()
        _write_daily_note(tmp_path, today, "---\n[10:00] (source: user, scope: main)\nOnly today")

        builder = _make_builder(tmp_path)
        result = builder._load_daily_notes(scope_key="main")

        assert "Only today" in result

    def test_no_files(self, tmp_path: Path) -> None:
        builder = _make_builder(tmp_path)
        result = builder._load_daily_notes(scope_key="main")

        assert result == ""

    def test_no_memory_dir(self, tmp_path: Path) -> None:
        builder = _make_builder(tmp_path)
        result = builder._load_daily_notes(scope_key="main")
        assert result == ""

    def test_truncation(self, tmp_path: Path) -> None:
        today = date.today()
        long_content = (
            "---\n[10:00] (source: user, scope: main)\n" + "A" * 20000
        )
        _write_daily_note(tmp_path, today, long_content)

        builder = _make_builder(tmp_path, daily_notes_max_tokens=100)
        result = builder._load_daily_notes(scope_key="main")

        assert "...(truncated)" in result
        # Should be limited (100 tokens * 4 chars = 400 chars + overhead)
        assert len(result) < 1000

    def test_scope_filtering_main(self, tmp_path: Path) -> None:
        """Entries with scope: main are included for main scope."""
        today = date.today()
        content = (
            "---\n[10:00] (source: user, scope: main)\nMain note\n"
            "---\n[11:00] (source: user, scope: other)\nOther note"
        )
        _write_daily_note(tmp_path, today, content)

        builder = _make_builder(tmp_path)
        result = builder._load_daily_notes(scope_key="main")

        assert "Main note" in result
        assert "Other note" not in result

    def test_old_data_compatibility(self, tmp_path: Path) -> None:
        """Entries without scope metadata are treated as scope='main'."""
        today = date.today()
        # Old format: no scope metadata
        content = "---\n[10:00] some old note without scope"
        _write_daily_note(tmp_path, today, content)

        builder = _make_builder(tmp_path)
        result = builder._load_daily_notes(scope_key="main")

        assert "some old note without scope" in result

    def test_old_data_excluded_for_non_main(self, tmp_path: Path) -> None:
        """Entries without scope metadata excluded for non-main scope."""
        today = date.today()
        content = "---\n[10:00] some old note without scope"
        _write_daily_note(tmp_path, today, content)

        builder = _make_builder(tmp_path)
        result = builder._load_daily_notes(scope_key="other")

        assert result == ""


class TestFilterEntriesByScope:
    def test_matching_scope(self) -> None:
        content = "---\n[10:00] (source: user, scope: main)\nMatched"
        result = PromptBuilder._filter_entries_by_scope(content, "main")
        assert "Matched" in result

    def test_non_matching_scope(self) -> None:
        content = "---\n[10:00] (source: user, scope: other)\nNot matched"
        result = PromptBuilder._filter_entries_by_scope(content, "main")
        assert result == ""

    def test_no_scope_metadata_as_main(self) -> None:
        content = "---\n[10:00] Old format entry"
        result = PromptBuilder._filter_entries_by_scope(content, "main")
        assert "Old format entry" in result

    def test_multiple_entries_mixed(self) -> None:
        content = (
            "---\n[10:00] (source: user, scope: main)\nEntry A\n"
            "---\n[11:00] (source: user, scope: other)\nEntry B\n"
            "---\n[12:00] (source: system, scope: main)\nEntry C"
        )
        result = PromptBuilder._filter_entries_by_scope(content, "main")
        assert "Entry A" in result
        assert "Entry B" not in result
        assert "Entry C" in result


class TestDailyNotesInBuild:
    def test_daily_notes_appear_in_workspace_layer(self, tmp_path: Path) -> None:
        """Daily notes are included in the workspace layer of the full prompt."""
        today = date.today()
        _write_daily_note(tmp_path, today, "---\n[10:00] (source: user, scope: main)\nImportant")

        from src.tools.base import ToolMode

        builder = _make_builder(tmp_path)
        prompt = builder.build("test-session", ToolMode.chat_safe, scope_key="main")

        assert "Important" in prompt
        assert "[Recent Daily Notes]" in prompt
