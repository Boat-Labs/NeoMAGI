"""Security boundary and functionality tests for ReadFileTool.

Covers: path traversal, prefix collision, type validation, symlink escape,
line range, output truncation, read state tracking, path alias.
"""

from __future__ import annotations

import pytest

from src.tools.builtins.read_file import ReadFileTool
from src.tools.context import ToolContext
from src.tools.read_state import ReadStateStore


@pytest.fixture()
def workspace(tmp_path):
    """Create an isolated workspace with test files."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "test.md").write_text("hello", encoding="utf-8")
    (ws / "subdir").mkdir()
    (ws / "subdir" / "nested.md").write_text("nested content", encoding="utf-8")
    # Multi-line file for range tests
    lines = "\n".join(f"line {i}" for i in range(1, 51))
    (ws / "multi.txt").write_text(lines, encoding="utf-8")
    return ws


@pytest.fixture()
def read_state_store():
    return ReadStateStore()


@pytest.fixture()
def tool(workspace, read_state_store):
    return ReadFileTool(workspace, read_state_store=read_state_store)


@pytest.fixture()
def ctx():
    return ToolContext(scope_key="main", session_id="test-session")


class TestReadFileHappyPath:
    @pytest.mark.asyncio()
    async def test_read_existing_file(self, tool, ctx):
        result = await tool.execute({"path": "test.md"}, ctx)
        assert "1\thello" in result["content"]
        assert result["relative_path"] == "test.md"
        assert result["total_lines"] == 1
        assert result["truncated"] is False
        assert result["size"] == 5

    @pytest.mark.asyncio()
    async def test_read_nested_file(self, tool, ctx):
        result = await tool.execute({"path": "subdir/nested.md"}, ctx)
        assert "nested content" in result["content"]
        assert result["relative_path"] == "subdir/nested.md"

    @pytest.mark.asyncio()
    async def test_file_path_preferred_over_path(self, tool, workspace, ctx):
        """file_path parameter takes precedence over path."""
        result = await tool.execute(
            {"file_path": str(workspace / "test.md"), "path": "subdir/nested.md"}, ctx
        )
        assert "hello" in result["content"]
        assert result["relative_path"] == "test.md"

    @pytest.mark.asyncio()
    async def test_absolute_file_path_within_workspace(self, tool, workspace, ctx):
        """Absolute path within workspace is accepted via file_path."""
        result = await tool.execute({"file_path": str(workspace / "test.md")}, ctx)
        assert "hello" in result["content"]
        assert result["file_path"] == str(workspace / "test.md")

    @pytest.mark.asyncio()
    async def test_returns_canonical_file_path(self, tool, workspace, ctx):
        result = await tool.execute({"path": "test.md"}, ctx)
        assert result["file_path"] == str(workspace / "test.md")


class TestReadFileLineRange:
    @pytest.mark.asyncio()
    async def test_offset_and_limit(self, tool, ctx):
        result = await tool.execute({"path": "multi.txt", "offset": 5, "limit": 3}, ctx)
        assert result["offset"] == 5
        assert result["lines_returned"] == 3
        assert result["total_lines"] == 50
        assert "6\tline 6" in result["content"]
        assert "8\tline 8" in result["content"]

    @pytest.mark.asyncio()
    async def test_truncation_with_default_limit(self, workspace, read_state_store, ctx):
        """File exceeding max_lines is truncated."""
        tool = ReadFileTool(workspace, read_state_store=read_state_store, max_lines=10)
        result = await tool.execute({"path": "multi.txt"}, ctx)
        assert result["truncated"] is True
        assert result["lines_returned"] == 10
        assert result["total_lines"] == 50

    @pytest.mark.asyncio()
    async def test_no_truncation_small_file(self, tool, ctx):
        result = await tool.execute({"path": "test.md"}, ctx)
        assert result["truncated"] is False
        assert result["lines_returned"] == 1

    @pytest.mark.asyncio()
    async def test_offset_beyond_file_returns_empty(self, tool, ctx):
        result = await tool.execute({"path": "multi.txt", "offset": 1000}, ctx)
        assert result["lines_returned"] == 0
        assert result["content"] == ""
        assert result["truncated"] is False


class TestReadFilePathTraversal:
    @pytest.mark.asyncio()
    async def test_absolute_path_outside_workspace_rejected(self, tool, ctx):
        result = await tool.execute({"file_path": "/etc/passwd"}, ctx)
        assert result["error_code"] == "ACCESS_DENIED"

    @pytest.mark.asyncio()
    async def test_dotdot_escape_rejected(self, tool, ctx):
        result = await tool.execute({"path": "../../etc/passwd"}, ctx)
        assert result["error_code"] == "ACCESS_DENIED"

    @pytest.mark.asyncio()
    async def test_prefix_collision_rejected(self, tmp_path):
        """Workspace /tmp/ws must not allow access to /tmp/ws-evil/."""
        ws = tmp_path / "ws"
        ws.mkdir()
        evil = tmp_path / "ws-evil"
        evil.mkdir()
        (evil / "secret.txt").write_text("TOPSECRET", encoding="utf-8")

        tool = ReadFileTool(ws)
        result = await tool.execute({"path": "../ws-evil/secret.txt"})
        assert result["error_code"] == "ACCESS_DENIED"

    @pytest.mark.asyncio()
    async def test_symlink_escape_rejected(self, workspace, tmp_path, read_state_store):
        """Symlink inside workspace pointing outside must be blocked."""
        external = tmp_path / "external"
        external.mkdir()
        (external / "secret.txt").write_text("TOPSECRET", encoding="utf-8")

        link = workspace / "escape_link"
        link.symlink_to(external / "secret.txt")

        tool = ReadFileTool(workspace, read_state_store=read_state_store)
        result = await tool.execute({"path": "escape_link"})
        assert result["error_code"] == "ACCESS_DENIED"


class TestReadFileInputValidation:
    @pytest.mark.asyncio()
    async def test_empty_path_rejected(self, tool):
        result = await tool.execute({"path": ""})
        assert result["error_code"] == "INVALID_ARGS"

    @pytest.mark.asyncio()
    async def test_null_path_rejected(self, tool):
        result = await tool.execute({"path": None})
        assert result["error_code"] == "INVALID_ARGS"

    @pytest.mark.asyncio()
    async def test_missing_path_rejected(self, tool):
        result = await tool.execute({})
        assert result["error_code"] == "INVALID_ARGS"

    @pytest.mark.asyncio()
    async def test_file_not_found(self, tool):
        result = await tool.execute({"path": "nonexistent.md"})
        assert result["error_code"] == "FILE_NOT_FOUND"

    @pytest.mark.asyncio()
    async def test_non_utf8_file(self, workspace, read_state_store):
        (workspace / "binary.dat").write_bytes(b"\xff\xfe\x00\x01")
        tool = ReadFileTool(workspace, read_state_store=read_state_store)
        result = await tool.execute({"path": "binary.dat"})
        assert result["error_code"] == "ENCODING_ERROR"


class TestReadFileReadState:
    @pytest.mark.asyncio()
    async def test_read_records_state(self, tool, workspace, read_state_store, ctx):
        await tool.execute({"path": "test.md"}, ctx)
        state = read_state_store.get("test-session", str(workspace / "test.md"))
        assert state is not None
        assert state.relative_path == "test.md"
        assert state.size == 5
        assert state.read_scope.offset == 0
        assert state.truncated is False

    @pytest.mark.asyncio()
    async def test_partial_read_state(self, tool, workspace, read_state_store, ctx):
        tool_small = ReadFileTool(workspace, read_state_store=read_state_store, max_lines=5)
        await tool_small.execute({"path": "multi.txt"}, ctx)
        state = read_state_store.get("test-session", str(workspace / "multi.txt"))
        assert state is not None
        assert state.truncated is True
        assert state.read_scope.offset == 0
        assert state.read_scope.limit == 5

    @pytest.mark.asyncio()
    async def test_staleness_check_fresh(self, tool, workspace, read_state_store, ctx):
        await tool.execute({"path": "test.md"}, ctx)
        stat = (workspace / "test.md").stat()
        err = read_state_store.check_staleness(
            "test-session", str(workspace / "test.md"),
            current_mtime_ns=stat.st_mtime_ns, current_size=stat.st_size,
        )
        assert err is None

    @pytest.mark.asyncio()
    async def test_staleness_check_modified(self, tool, workspace, read_state_store, ctx):
        await tool.execute({"path": "test.md"}, ctx)
        # Modify the file
        (workspace / "test.md").write_text("modified", encoding="utf-8")
        stat = (workspace / "test.md").stat()
        err = read_state_store.check_staleness(
            "test-session", str(workspace / "test.md"),
            current_mtime_ns=stat.st_mtime_ns, current_size=stat.st_size,
        )
        assert err == "STALE_READ"
