"""Tests for WriteFileTool and EditFileTool.

Covers: create/update semantics, overwrite enforcement, staleness check,
partial read rejection, edit exact match, replace_all, workspace boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.tools.builtins.edit_file import EditFileTool
from src.tools.builtins.read_file import ReadFileTool
from src.tools.builtins.write_file import WriteFileTool
from src.tools.context import ToolContext
from src.tools.read_state import ReadScope, ReadState, ReadStateStore


@pytest.fixture()
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "existing.txt").write_text("hello world\n", encoding="utf-8")
    (ws / "multi.txt").write_text("line1\nline2\nline1\n", encoding="utf-8")
    return ws


@pytest.fixture()
def read_state_store():
    return ReadStateStore()


@pytest.fixture()
def read_tool(workspace, read_state_store):
    return ReadFileTool(workspace, read_state_store=read_state_store)


@pytest.fixture()
def write_tool(workspace, read_state_store):
    return WriteFileTool(workspace, read_state_store=read_state_store)


@pytest.fixture()
def edit_tool(workspace, read_state_store):
    return EditFileTool(workspace, read_state_store=read_state_store)


@pytest.fixture()
def ctx():
    return ToolContext(scope_key="main", session_id="test-session")


def _record_full_read(store, workspace, filename, session_id="test-session"):
    """Helper: record a full read state for a file."""
    path = workspace / filename
    stat = path.stat()
    store.record(ReadState(
        session_id=session_id,
        file_path=str(path),
        relative_path=filename,
        mtime_ns=stat.st_mtime_ns,
        size=stat.st_size,
        read_scope=ReadScope(offset=0, limit=None),
        truncated=False,
        read_at=datetime.now(UTC),
    ))


def _record_partial_read(store, workspace, filename, session_id="test-session"):
    """Helper: record a partial (truncated) read state."""
    path = workspace / filename
    stat = path.stat()
    store.record(ReadState(
        session_id=session_id,
        file_path=str(path),
        relative_path=filename,
        mtime_ns=stat.st_mtime_ns,
        size=stat.st_size,
        read_scope=ReadScope(offset=0, limit=1),
        truncated=True,
        read_at=datetime.now(UTC),
    ))


# ========== WriteFileTool Tests ==========


class TestWriteFileCreate:
    @pytest.mark.asyncio()
    async def test_create_new_file(self, write_tool, workspace, ctx):
        result = await write_tool.execute(
            {"file_path": str(workspace / "new.txt"), "content": "new content"}, ctx
        )
        assert result["ok"] is True
        assert result["operation"] == "create"
        assert result["relative_path"] == "new.txt"
        assert (workspace / "new.txt").read_text(encoding="utf-8") == "new content"

    @pytest.mark.asyncio()
    async def test_create_in_subdir(self, write_tool, workspace, ctx):
        result = await write_tool.execute(
            {"file_path": str(workspace / "sub/dir/new.py"), "content": "# code"}, ctx
        )
        assert result["ok"] is True
        assert result["operation"] == "create"
        assert (workspace / "sub" / "dir" / "new.py").read_text(encoding="utf-8") == "# code"

    @pytest.mark.asyncio()
    async def test_create_returns_size(self, write_tool, workspace, ctx):
        result = await write_tool.execute(
            {"file_path": str(workspace / "sized.txt"), "content": "hello"}, ctx
        )
        assert result["size"] == 5


class TestWriteFileOverwrite:
    @pytest.mark.asyncio()
    async def test_existing_without_overwrite_fails(self, write_tool, workspace, ctx):
        result = await write_tool.execute(
            {"file_path": str(workspace / "existing.txt"), "content": "new"}, ctx
        )
        assert result["error_code"] == "FILE_EXISTS"

    @pytest.mark.asyncio()
    async def test_overwrite_after_full_read(self, write_tool, workspace, read_state_store, ctx):
        _record_full_read(read_state_store, workspace, "existing.txt")
        result = await write_tool.execute(
            {
                "file_path": str(workspace / "existing.txt"),
                "content": "replaced",
                "overwrite": True,
            },
            ctx,
        )
        assert result["ok"] is True
        assert result["operation"] == "update"
        assert (workspace / "existing.txt").read_text(encoding="utf-8") == "replaced"

    @pytest.mark.asyncio()
    async def test_overwrite_returns_update_operation(
        self, write_tool, workspace, read_state_store, ctx
    ):
        _record_full_read(read_state_store, workspace, "existing.txt")
        result = await write_tool.execute(
            {"file_path": str(workspace / "existing.txt"), "content": "x", "overwrite": True}, ctx
        )
        assert result["operation"] == "update"


class TestWriteFileReadStateChecks:
    @pytest.mark.asyncio()
    async def test_overwrite_without_read_fails(self, write_tool, workspace, ctx):
        result = await write_tool.execute(
            {"file_path": str(workspace / "existing.txt"), "content": "new", "overwrite": True},
            ctx,
        )
        assert result["error_code"] == "READ_REQUIRED"

    @pytest.mark.asyncio()
    async def test_overwrite_stale_read_fails(self, write_tool, workspace, read_state_store, ctx):
        _record_full_read(read_state_store, workspace, "existing.txt")
        # Modify file after read
        (workspace / "existing.txt").write_text("modified externally", encoding="utf-8")
        result = await write_tool.execute(
            {"file_path": str(workspace / "existing.txt"), "content": "new", "overwrite": True},
            ctx,
        )
        assert result["error_code"] == "STALE_READ"

    @pytest.mark.asyncio()
    async def test_overwrite_with_partial_read_fails(
        self, write_tool, workspace, read_state_store, ctx
    ):
        _record_partial_read(read_state_store, workspace, "existing.txt")
        result = await write_tool.execute(
            {"file_path": str(workspace / "existing.txt"), "content": "new", "overwrite": True},
            ctx,
        )
        assert result["error_code"] == "PARTIAL_READ"


class TestWriteFileBoundary:
    @pytest.mark.asyncio()
    async def test_path_escape_rejected(self, write_tool, ctx):
        result = await write_tool.execute(
            {"file_path": "/etc/evil.txt", "content": "bad"}, ctx
        )
        assert result["error_code"] == "ACCESS_DENIED"

    @pytest.mark.asyncio()
    async def test_empty_path_rejected(self, write_tool, ctx):
        result = await write_tool.execute({"file_path": "", "content": "x"}, ctx)
        assert result["error_code"] == "INVALID_ARGS"

    @pytest.mark.asyncio()
    async def test_missing_content_rejected(self, write_tool, workspace, ctx):
        result = await write_tool.execute(
            {"file_path": str(workspace / "x.txt")}, ctx
        )
        assert result["error_code"] == "INVALID_ARGS"


class TestWriteFileIntegration:
    """Integration: read_file → write_file round-trip."""

    @pytest.mark.asyncio()
    async def test_read_then_overwrite(self, read_tool, write_tool, workspace, ctx):
        # Read first
        read_result = await read_tool.execute({"path": "existing.txt"}, ctx)
        assert "hello world" in read_result["content"]

        # Now overwrite
        write_result = await write_tool.execute(
            {"file_path": str(workspace / "existing.txt"), "content": "updated", "overwrite": True},
            ctx,
        )
        assert write_result["ok"] is True
        assert write_result["operation"] == "update"


# ========== EditFileTool Tests ==========


class TestEditFileHappyPath:
    @pytest.mark.asyncio()
    async def test_unique_match_edit(self, edit_tool, workspace, read_state_store, ctx):
        _record_full_read(read_state_store, workspace, "existing.txt")
        result = await edit_tool.execute(
            {
                "file_path": str(workspace / "existing.txt"),
                "old_string": "hello world",
                "new_string": "goodbye world",
            },
            ctx,
        )
        assert result["ok"] is True
        assert result["replacements"] == 1
        assert (workspace / "existing.txt").read_text(encoding="utf-8") == "goodbye world\n"

    @pytest.mark.asyncio()
    async def test_replace_all(self, edit_tool, workspace, read_state_store, ctx):
        _record_full_read(read_state_store, workspace, "multi.txt")
        result = await edit_tool.execute(
            {
                "file_path": str(workspace / "multi.txt"),
                "old_string": "line1",
                "new_string": "LINE_ONE",
                "replace_all": True,
            },
            ctx,
        )
        assert result["ok"] is True
        assert result["replacements"] == 2
        content = (workspace / "multi.txt").read_text(encoding="utf-8")
        assert content.count("LINE_ONE") == 2
        assert "line1" not in content


class TestEditFileFailFast:
    @pytest.mark.asyncio()
    async def test_no_match(self, edit_tool, workspace, read_state_store, ctx):
        _record_full_read(read_state_store, workspace, "existing.txt")
        result = await edit_tool.execute(
            {
                "file_path": str(workspace / "existing.txt"),
                "old_string": "nonexistent",
                "new_string": "x",
            },
            ctx,
        )
        assert result["error_code"] == "NO_MATCH"

    @pytest.mark.asyncio()
    async def test_multiple_matches_without_replace_all(
        self, edit_tool, workspace, read_state_store, ctx
    ):
        _record_full_read(read_state_store, workspace, "multi.txt")
        result = await edit_tool.execute(
            {
                "file_path": str(workspace / "multi.txt"),
                "old_string": "line1",
                "new_string": "x",
            },
            ctx,
        )
        assert result["error_code"] == "MULTIPLE_MATCHES"
        assert "2 times" in result["message"]

    @pytest.mark.asyncio()
    async def test_replace_all_zero_match_fails(
        self, edit_tool, workspace, read_state_store, ctx
    ):
        _record_full_read(read_state_store, workspace, "existing.txt")
        result = await edit_tool.execute(
            {
                "file_path": str(workspace / "existing.txt"),
                "old_string": "nonexistent",
                "new_string": "x",
                "replace_all": True,
            },
            ctx,
        )
        assert result["error_code"] == "NO_MATCH"

    @pytest.mark.asyncio()
    async def test_identical_strings_rejected(
        self, edit_tool, workspace, read_state_store, ctx
    ):
        _record_full_read(read_state_store, workspace, "existing.txt")
        result = await edit_tool.execute(
            {
                "file_path": str(workspace / "existing.txt"),
                "old_string": "hello",
                "new_string": "hello",
            },
            ctx,
        )
        assert result["error_code"] == "INVALID_ARGS"


class TestEditFileReadStateChecks:
    @pytest.mark.asyncio()
    async def test_edit_without_read_fails(self, edit_tool, workspace, ctx):
        result = await edit_tool.execute(
            {
                "file_path": str(workspace / "existing.txt"),
                "old_string": "hello",
                "new_string": "bye",
            },
            ctx,
        )
        assert result["error_code"] == "READ_REQUIRED"

    @pytest.mark.asyncio()
    async def test_edit_stale_read_fails(self, edit_tool, workspace, read_state_store, ctx):
        _record_full_read(read_state_store, workspace, "existing.txt")
        (workspace / "existing.txt").write_text("modified", encoding="utf-8")
        result = await edit_tool.execute(
            {
                "file_path": str(workspace / "existing.txt"),
                "old_string": "hello",
                "new_string": "bye",
            },
            ctx,
        )
        assert result["error_code"] == "STALE_READ"

    @pytest.mark.asyncio()
    async def test_edit_with_partial_read_ok(self, edit_tool, workspace, read_state_store, ctx):
        """edit_file does NOT require full read — partial read is sufficient."""
        _record_partial_read(read_state_store, workspace, "existing.txt")
        result = await edit_tool.execute(
            {
                "file_path": str(workspace / "existing.txt"),
                "old_string": "hello world",
                "new_string": "goodbye world",
            },
            ctx,
        )
        assert result["ok"] is True


class TestEditFileBoundary:
    @pytest.mark.asyncio()
    async def test_path_escape_rejected(self, edit_tool, ctx):
        result = await edit_tool.execute(
            {"file_path": "/etc/passwd", "old_string": "x", "new_string": "y"}, ctx
        )
        assert result["error_code"] == "ACCESS_DENIED"

    @pytest.mark.asyncio()
    async def test_file_not_found(self, edit_tool, workspace, ctx):
        result = await edit_tool.execute(
            {"file_path": str(workspace / "nope.txt"), "old_string": "x", "new_string": "y"}, ctx
        )
        assert result["error_code"] == "FILE_NOT_FOUND"

    @pytest.mark.asyncio()
    async def test_empty_path_rejected(self, edit_tool, ctx):
        result = await edit_tool.execute(
            {"file_path": "", "old_string": "x", "new_string": "y"}, ctx
        )
        assert result["error_code"] == "INVALID_ARGS"


class TestEditFileIntegration:
    """Integration: read_file → edit_file round-trip."""

    @pytest.mark.asyncio()
    async def test_read_then_edit(self, read_tool, edit_tool, workspace, ctx):
        # Read first
        read_result = await read_tool.execute({"path": "existing.txt"}, ctx)
        assert "hello world" in read_result["content"]

        # Edit
        edit_result = await edit_tool.execute(
            {
                "file_path": str(workspace / "existing.txt"),
                "old_string": "hello world",
                "new_string": "goodbye world",
            },
            ctx,
        )
        assert edit_result["ok"] is True
        assert (workspace / "existing.txt").read_text(encoding="utf-8") == "goodbye world\n"
