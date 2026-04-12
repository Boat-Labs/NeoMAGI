"""P2-M3b visibility policy tests.

Covers the missing test coverage identified in post-review:
- Searcher cross-principal / anonymous / shared_in_space filtering
- PromptBuilder principal + visibility 3-way equivalence
- memory_append writes context.principal_id
- memory_search passes context.principal_id
- Compaction flush / procedure publish flush carry principal_id
- Workspace reindex preserves principal_id + visibility from projection
- _parse_entry_metadata returns principal + visibility fields
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.prompt_builder import PromptBuilder
from src.config.settings import MemorySettings
from src.memory.contracts import ResolvedFlushCandidate
from src.memory.indexer import MemoryIndexer
from src.memory.searcher import MemorySearcher
from src.memory.writer import MemoryWriter
from src.tools.builtins.memory_append import MemoryAppendTool
from src.tools.builtins.memory_search import MemorySearchTool
from src.tools.context import ToolContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(workspace: Path) -> MemorySettings:
    return MemorySettings(
        workspace_path=workspace,
        max_daily_note_bytes=32_768,
        daily_notes_load_days=2,
        daily_notes_max_tokens=4000,
        flush_min_confidence=0.5,
    )


def _write_daily_note(workspace: Path, target_date: date, content: str) -> Path:
    memory_dir = workspace / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    fp = memory_dir / f"{target_date.isoformat()}.md"
    fp.write_text(content, encoding="utf-8")
    return fp


def _make_entry(
    text: str,
    *,
    entry_id: str = "e1",
    source: str = "user",
    scope: str = "main",
    principal: str | None = None,
    visibility: str | None = None,
    session_id: str | None = None,
) -> str:
    parts = [f"entry_id: {entry_id}", f"source: {source}", f"scope: {scope}"]
    if principal is not None:
        parts.append(f"principal: {principal}")
    if visibility is not None:
        parts.append(f"visibility: {visibility}")
    if session_id:
        parts.append(f"source_session_id: {session_id}")
    meta = f"[10:00] ({', '.join(parts)})"
    return f"---\n{meta}\n{text}\n"


# ===========================================================================
# Searcher: _build_search_sql principal + visibility WHERE
# ===========================================================================

class TestSearcherBuildSql:
    """Unit tests for the SQL WHERE conditions (no DB needed)."""

    def test_authenticated_principal_sees_own_and_legacy(self) -> None:
        sql, params = MemorySearcher._build_search_sql(
            "test", scope_key="main", limit=10, min_score=0.0,
            source_types=None, principal_id="p-owner",
        )
        assert "principal_id = :principal_id OR principal_id IS NULL" in sql
        assert params["principal_id"] == "p-owner"
        assert "visibility IN ('private_to_principal', 'shareable_summary')" in sql

    def test_anonymous_only_sees_null_principal(self) -> None:
        sql, params = MemorySearcher._build_search_sql(
            "test", scope_key="main", limit=10, min_score=0.0,
            source_types=None, principal_id=None,
        )
        assert "principal_id IS NULL" in sql
        assert "principal_id =" not in sql.replace("principal_id IS NULL", "")
        assert "principal_id" not in params

    def test_shared_in_space_always_excluded(self) -> None:
        sql, _ = MemorySearcher._build_search_sql(
            "test", scope_key="main", limit=10, min_score=0.0,
            source_types=None, principal_id="p-owner",
        )
        assert "shared_in_space" not in sql


# ===========================================================================
# PromptBuilder: _filter_entries 3-way equivalence
# ===========================================================================

class TestPromptFilterEntries:
    """PromptBuilder._filter_entries must match D5 searcher WHERE semantics."""

    def test_own_principal_visible(self) -> None:
        content = _make_entry("my note", principal="p1", visibility="private_to_principal")
        result = PromptBuilder._filter_entries(content, "main", principal_id="p1")
        assert "my note" in result

    def test_cross_principal_hidden(self) -> None:
        content = _make_entry("other note", principal="p2", visibility="private_to_principal")
        result = PromptBuilder._filter_entries(content, "main", principal_id="p1")
        assert "other note" not in result

    def test_anonymous_cannot_see_principal_tagged(self) -> None:
        content = _make_entry("owned note", principal="p1", visibility="private_to_principal")
        result = PromptBuilder._filter_entries(content, "main", principal_id=None)
        assert "owned note" not in result

    def test_anonymous_sees_legacy_no_principal(self) -> None:
        content = _make_entry("legacy note")
        result = PromptBuilder._filter_entries(content, "main", principal_id=None)
        assert "legacy note" in result

    def test_authenticated_sees_legacy_no_principal(self) -> None:
        content = _make_entry("legacy note")
        result = PromptBuilder._filter_entries(content, "main", principal_id="p1")
        assert "legacy note" in result

    def test_shared_in_space_denied(self) -> None:
        content = _make_entry("shared note", principal="p1", visibility="shared_in_space")
        result = PromptBuilder._filter_entries(content, "main", principal_id="p1")
        assert "shared note" not in result

    def test_unknown_visibility_denied(self) -> None:
        content = _make_entry("future note", principal="p1", visibility="future_type")
        result = PromptBuilder._filter_entries(content, "main", principal_id="p1")
        assert "future note" not in result

    def test_no_visibility_tag_treated_as_private(self) -> None:
        content = _make_entry("old note")
        result = PromptBuilder._filter_entries(content, "main", principal_id="p1")
        assert "old note" in result

    def test_shareable_summary_visible(self) -> None:
        content = _make_entry("summary", principal="p1", visibility="shareable_summary")
        result = PromptBuilder._filter_entries(content, "main", principal_id="p1")
        assert "summary" in result

    def test_load_daily_notes_passes_principal(self, tmp_path: Path) -> None:
        """_load_daily_notes propagates principal_id to _filter_entries."""
        today = date.today()
        _write_daily_note(tmp_path, today, (
            _make_entry("owner note", principal="p1", visibility="private_to_principal")
            + _make_entry("other note", principal="p2", visibility="private_to_principal")
            + _make_entry("legacy note")
        ))
        builder = PromptBuilder(tmp_path, memory_settings=_make_settings(tmp_path))
        result = builder._load_daily_notes(scope_key="main", principal_id="p1")
        assert "owner note" in result
        assert "other note" not in result
        assert "legacy note" in result


# ===========================================================================
# memory_append: writes context.principal_id
# ===========================================================================

class TestMemoryAppendPrincipal:
    @pytest.mark.asyncio
    async def test_principal_id_written_to_projection(self, tmp_path: Path) -> None:
        """memory_append with principal_id renders it in workspace metadata."""
        settings = _make_settings(tmp_path)
        writer = MemoryWriter(tmp_path, settings)
        tool = MemoryAppendTool(writer)
        ctx = ToolContext(scope_key="main", session_id="s1", principal_id="p-owner")

        result = await tool.execute({"text": "remember this"}, ctx)
        assert result["ok"] is True

        # Check workspace file contains principal metadata
        today = date.today()
        fp = tmp_path / "memory" / f"{today.isoformat()}.md"
        content = fp.read_text(encoding="utf-8")
        assert "principal: p-owner" in content
        assert "visibility: private_to_principal" in content

    @pytest.mark.asyncio
    async def test_no_principal_omits_field(self, tmp_path: Path) -> None:
        """Anonymous write must NOT render 'principal:' at all."""
        settings = _make_settings(tmp_path)
        writer = MemoryWriter(tmp_path, settings)
        tool = MemoryAppendTool(writer)
        ctx = ToolContext(scope_key="main", session_id="s1")

        await tool.execute({"text": "anon note"}, ctx)

        today = date.today()
        fp = tmp_path / "memory" / f"{today.isoformat()}.md"
        content = fp.read_text(encoding="utf-8")
        assert "principal:" not in content
        assert "visibility: private_to_principal" in content


# ===========================================================================
# memory_search: passes context.principal_id
# ===========================================================================

class TestMemorySearchPrincipal:
    @pytest.mark.asyncio
    async def test_passes_principal_to_searcher(self) -> None:
        searcher = MagicMock()
        searcher.search = AsyncMock(return_value=[])
        tool = MemorySearchTool(searcher=searcher)
        ctx = ToolContext(scope_key="main", session_id="s1", principal_id="p-owner")

        await tool.execute({"query": "test"}, ctx)

        searcher.search.assert_called_once_with(
            query="test", scope_key="main", limit=10, principal_id="p-owner",
        )

    @pytest.mark.asyncio
    async def test_no_context_passes_none(self) -> None:
        searcher = MagicMock()
        searcher.search = AsyncMock(return_value=[])
        tool = MemorySearchTool(searcher=searcher)

        await tool.execute({"query": "test"}, None)

        searcher.search.assert_called_once_with(
            query="test", scope_key="main", limit=10, principal_id=None,
        )


# ===========================================================================
# Flush candidates carry principal_id
# ===========================================================================

class TestFlushPrincipal:
    @pytest.mark.asyncio
    async def test_process_flush_candidates_passes_principal(self, tmp_path: Path) -> None:
        """MemoryWriter.process_flush_candidates propagates candidate.principal_id."""
        settings = _make_settings(tmp_path)
        writer = MemoryWriter(tmp_path, settings)

        candidates = [
            ResolvedFlushCandidate(
                candidate_text="flushed memory",
                scope_key="main",
                source_session_id="s1",
                confidence=0.9,
                principal_id="p-owner",
            ),
        ]
        written = await writer.process_flush_candidates(candidates)
        assert written == 1

        today = date.today()
        fp = tmp_path / "memory" / f"{today.isoformat()}.md"
        content = fp.read_text(encoding="utf-8")
        assert "principal: p-owner" in content
        assert "visibility: private_to_principal" in content

    @pytest.mark.asyncio
    async def test_flush_without_principal_omits_field(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        writer = MemoryWriter(tmp_path, settings)

        candidates = [
            ResolvedFlushCandidate(
                candidate_text="anon flush",
                scope_key="main",
                source_session_id="s1",
                confidence=0.9,
            ),
        ]
        written = await writer.process_flush_candidates(candidates)
        assert written == 1

        today = date.today()
        fp = tmp_path / "memory" / f"{today.isoformat()}.md"
        content = fp.read_text(encoding="utf-8")
        assert "principal:" not in content


# ===========================================================================
# Writer visibility fail-closed
# ===========================================================================

class TestWriterVisibility:
    @pytest.mark.asyncio
    async def test_shared_in_space_rejected(self, tmp_path: Path) -> None:
        from src.infra.errors import MemoryWriteError

        settings = _make_settings(tmp_path)
        writer = MemoryWriter(tmp_path, settings)

        with pytest.raises(MemoryWriteError, match="not yet writable"):
            await writer.append_daily_note(
                "test", scope_key="main", visibility="shared_in_space",
            )

    @pytest.mark.asyncio
    async def test_unknown_visibility_rejected(self, tmp_path: Path) -> None:
        from src.infra.errors import MemoryWriteError

        settings = _make_settings(tmp_path)
        writer = MemoryWriter(tmp_path, settings)

        with pytest.raises(MemoryWriteError, match="Unknown visibility"):
            await writer.append_daily_note(
                "test", scope_key="main", visibility="bogus",
            )

    @pytest.mark.asyncio
    async def test_shareable_summary_accepted(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path)
        writer = MemoryWriter(tmp_path, settings)

        result = await writer.append_daily_note(
            "summary", scope_key="main", visibility="shareable_summary",
        )
        assert result.projection_written is True


# ===========================================================================
# _parse_entry_metadata returns principal + visibility
# ===========================================================================

class TestParseEntryMetadata:
    def test_with_principal_and_visibility(self) -> None:
        line = (
            "[10:00] (entry_id: e1, source: user, scope: main, "
            "principal: p-owner, visibility: shareable_summary)\ntext"
        )
        meta = MemoryIndexer._parse_entry_metadata(line)
        assert meta["principal"] == "p-owner"
        assert meta["visibility"] == "shareable_summary"

    def test_without_principal_returns_none(self) -> None:
        line = "[10:00] (entry_id: e1, source: user, scope: main)\ntext"
        meta = MemoryIndexer._parse_entry_metadata(line)
        assert meta["principal"] is None
        assert meta["visibility"] == "private_to_principal"

    def test_no_metadata_line(self) -> None:
        meta = MemoryIndexer._parse_entry_metadata("plain text without metadata")
        assert meta["principal"] is None
        assert meta["visibility"] == "private_to_principal"


# ===========================================================================
# Workspace reindex preserves principal_id + visibility (P2 fix #1 regression)
# ===========================================================================

class TestWorkspaceReindexPreservesVisibility:
    def test_parse_daily_entries_includes_principal_and_visibility(self) -> None:
        """_parse_daily_entries must propagate principal + visibility to row dict."""
        indexer_settings = _make_settings(Path("/tmp/test"))
        indexer = MemoryIndexer(MagicMock(), indexer_settings)

        entries = [
            "[10:00] (entry_id: e1, source: user, scope: main, "
            "principal: p-owner, visibility: private_to_principal)\n"
            "my note",
        ]
        rows = indexer._parse_daily_entries(entries, "main", date.today(), "memory/test.md")
        assert len(rows) == 1
        assert rows[0]["principal_id"] == "p-owner"
        assert rows[0]["visibility"] == "private_to_principal"

    def test_parse_daily_entries_legacy_no_principal(self) -> None:
        """Legacy entries without principal/visibility get None + default."""
        indexer_settings = _make_settings(Path("/tmp/test"))
        indexer = MemoryIndexer(MagicMock(), indexer_settings)

        entries = [
            "[10:00] (entry_id: e2, source: user, scope: main)\nold note",
        ]
        rows = indexer._parse_daily_entries(entries, "main", date.today(), "memory/test.md")
        assert len(rows) == 1
        assert rows[0]["principal_id"] is None
        assert rows[0]["visibility"] == "private_to_principal"
