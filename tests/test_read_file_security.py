"""Security boundary tests for ReadFileTool.

Covers: path traversal, prefix collision, type validation, symlink escape.
"""

from __future__ import annotations

import pytest

from src.tools.builtins.read_file import ReadFileTool


@pytest.fixture()
def workspace(tmp_path):
    """Create an isolated workspace with a test file."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "test.md").write_text("hello", encoding="utf-8")
    (ws / "subdir").mkdir()
    (ws / "subdir" / "nested.md").write_text("nested content", encoding="utf-8")
    return ws


@pytest.fixture()
def tool(workspace):
    return ReadFileTool(workspace)


class TestReadFileHappyPath:
    @pytest.mark.asyncio()
    async def test_read_existing_file(self, tool):
        result = await tool.execute({"path": "test.md"})
        assert result["content"] == "hello"
        assert result["path"] == "test.md"
        assert result["size"] == 5

    @pytest.mark.asyncio()
    async def test_read_nested_file(self, tool):
        result = await tool.execute({"path": "subdir/nested.md"})
        assert result["content"] == "nested content"


class TestReadFilePathTraversal:
    @pytest.mark.asyncio()
    async def test_absolute_path_rejected(self, tool):
        result = await tool.execute({"path": "/etc/passwd"})
        assert result["error_code"] == "ACCESS_DENIED"

    @pytest.mark.asyncio()
    async def test_dotdot_escape_rejected(self, tool):
        result = await tool.execute({"path": "../../etc/passwd"})
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
        # Craft a relative path that resolves to ws-evil via ..
        # ws/../../ws-evil/secret.txt -> tmp_path/ws-evil/secret.txt
        result = await tool.execute({"path": "../ws-evil/secret.txt"})
        assert result["error_code"] == "ACCESS_DENIED"

    @pytest.mark.asyncio()
    async def test_symlink_escape_rejected(self, workspace, tmp_path):
        """Symlink inside workspace pointing outside must be blocked."""
        external = tmp_path / "external"
        external.mkdir()
        (external / "secret.txt").write_text("TOPSECRET", encoding="utf-8")

        link = workspace / "escape_link"
        link.symlink_to(external / "secret.txt")

        tool = ReadFileTool(workspace)
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
