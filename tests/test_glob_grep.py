"""Tests for GlobTool and GrepTool.

Covers: basic matching, workspace boundary, output truncation,
symlink escape, regex errors, case insensitive search.
"""

from __future__ import annotations

import pytest

from src.tools.builtins.glob_tool import GlobTool
from src.tools.builtins.grep_tool import GrepTool


@pytest.fixture()
def workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "hello.py").write_text("def greet():\n    return 'hello'\n", encoding="utf-8")
    (ws / "world.py").write_text("def world():\n    return 'world'\n", encoding="utf-8")
    (ws / "readme.md").write_text("# README\nThis is a test project.\n", encoding="utf-8")
    sub = ws / "src"
    sub.mkdir()
    (sub / "main.py").write_text("import hello\nimport world\n", encoding="utf-8")
    (sub / "utils.ts").write_text("export const foo = 42;\n", encoding="utf-8")
    return ws


# ---- Glob Tests ----

class TestGlobHappyPath:
    @pytest.mark.asyncio()
    async def test_glob_all_python(self, workspace):
        tool = GlobTool(workspace)
        result = await tool.execute({"pattern": "**/*.py"})
        assert result["count"] >= 3
        assert not result["truncated"]
        assert any("hello.py" in m for m in result["matches"])
        assert any("src/main.py" in m for m in result["matches"])

    @pytest.mark.asyncio()
    async def test_glob_specific_dir(self, workspace):
        tool = GlobTool(workspace)
        result = await tool.execute({"pattern": "*.py", "path": "src"})
        assert result["count"] == 1
        assert "main.py" in result["matches"][0]

    @pytest.mark.asyncio()
    async def test_glob_markdown(self, workspace):
        tool = GlobTool(workspace)
        result = await tool.execute({"pattern": "*.md"})
        assert result["count"] == 1
        assert "readme.md" in result["matches"][0]

    @pytest.mark.asyncio()
    async def test_glob_no_matches(self, workspace):
        tool = GlobTool(workspace)
        result = await tool.execute({"pattern": "*.java"})
        assert result["count"] == 0
        assert result["matches"] == []


class TestGlobBoundary:
    @pytest.mark.asyncio()
    async def test_path_escape_rejected(self, workspace):
        tool = GlobTool(workspace)
        result = await tool.execute({"pattern": "*.py", "path": "../../"})
        assert result["error_code"] == "ACCESS_DENIED"

    @pytest.mark.asyncio()
    async def test_empty_pattern_rejected(self, workspace):
        tool = GlobTool(workspace)
        result = await tool.execute({"pattern": ""})
        assert result["error_code"] == "INVALID_ARGS"

    @pytest.mark.asyncio()
    async def test_nonexistent_dir(self, workspace):
        tool = GlobTool(workspace)
        result = await tool.execute({"pattern": "*.py", "path": "nonexistent"})
        assert result["error_code"] == "INVALID_ARGS"

    @pytest.mark.asyncio()
    async def test_truncation(self, workspace):
        tool = GlobTool(workspace, max_results=2)
        result = await tool.execute({"pattern": "**/*"})
        assert result["truncated"] is True
        assert result["count"] == 2

    @pytest.mark.asyncio()
    async def test_symlink_escape_filtered(self, workspace, tmp_path):
        external = tmp_path / "external"
        external.mkdir()
        (external / "secret.py").write_text("SECRET", encoding="utf-8")
        (workspace / "link.py").symlink_to(external / "secret.py")

        tool = GlobTool(workspace)
        result = await tool.execute({"pattern": "*.py"})
        # link.py resolves outside workspace → filtered out
        for m in result["matches"]:
            assert "secret" not in m


# ---- Grep Tests ----

class TestGrepHappyPath:
    @pytest.mark.asyncio()
    async def test_grep_literal(self, workspace):
        tool = GrepTool(workspace)
        result = await tool.execute({"pattern": "greet"})
        assert result["count"] >= 1
        assert result["matches"][0]["file"] == "hello.py"
        assert result["matches"][0]["line"] == 1

    @pytest.mark.asyncio()
    async def test_grep_regex(self, workspace):
        tool = GrepTool(workspace)
        result = await tool.execute({"pattern": r"def \w+\(\)"})
        assert result["count"] >= 2

    @pytest.mark.asyncio()
    async def test_grep_case_insensitive(self, workspace):
        tool = GrepTool(workspace)
        result = await tool.execute({"pattern": "README", "case_insensitive": True})
        assert result["count"] >= 1

    @pytest.mark.asyncio()
    async def test_grep_glob_filter(self, workspace):
        tool = GrepTool(workspace)
        result = await tool.execute({"pattern": "import", "glob": "**/*.py"})
        assert result["count"] >= 1
        for m in result["matches"]:
            assert m["file"].endswith(".py")

    @pytest.mark.asyncio()
    async def test_grep_in_subdir(self, workspace):
        tool = GrepTool(workspace)
        result = await tool.execute({"pattern": "import", "path": "src"})
        assert result["count"] >= 1

    @pytest.mark.asyncio()
    async def test_grep_no_matches(self, workspace):
        tool = GrepTool(workspace)
        result = await tool.execute({"pattern": "nonexistent_pattern_xyz"})
        assert result["count"] == 0


class TestGrepBoundary:
    @pytest.mark.asyncio()
    async def test_invalid_regex(self, workspace):
        tool = GrepTool(workspace)
        result = await tool.execute({"pattern": "[invalid"})
        assert result["error_code"] == "INVALID_PATTERN"

    @pytest.mark.asyncio()
    async def test_empty_pattern_rejected(self, workspace):
        tool = GrepTool(workspace)
        result = await tool.execute({"pattern": ""})
        assert result["error_code"] == "INVALID_ARGS"

    @pytest.mark.asyncio()
    async def test_path_escape_rejected(self, workspace):
        tool = GrepTool(workspace)
        result = await tool.execute({"pattern": "x", "path": "../../"})
        assert result["error_code"] == "ACCESS_DENIED"

    @pytest.mark.asyncio()
    async def test_truncation(self, workspace):
        tool = GrepTool(workspace, max_results=2)
        result = await tool.execute({"pattern": "."})
        assert result["truncated"] is True
        assert result["count"] == 2

    @pytest.mark.asyncio()
    async def test_binary_files_skipped(self, workspace):
        (workspace / "binary.dat").write_bytes(b"\xff\xfe\x00\x01")
        tool = GrepTool(workspace)
        result = await tool.execute({"pattern": ".", "glob": "*.dat"})
        assert result["count"] == 0
