"""Tests for src/procedures/result.py — ToolResult and normalize_tool_result."""

from __future__ import annotations

from src.procedures.result import ToolResult, normalize_tool_result


class TestToolResult:
    def test_defaults(self) -> None:
        r = ToolResult()
        assert r.ok is True
        assert r.data == {}
        assert r.context_patch == {}

    def test_explicit(self) -> None:
        r = ToolResult(ok=False, data={"x": 1}, context_patch={"y": 2})
        assert r.ok is False
        assert r.data == {"x": 1}
        assert r.context_patch == {"y": 2}


class TestNormalizeToolResult:
    def test_passthrough_tool_result(self) -> None:
        original = ToolResult(ok=True, data={"a": 1}, context_patch={"b": 2})
        result = normalize_tool_result(original)
        assert result is original

    def test_dict_with_context_patch(self) -> None:
        raw = {"status": "done", "context_patch": {"key": "val"}, "extra": 42}
        result = normalize_tool_result(raw)
        assert result.ok is True
        assert result.context_patch == {"key": "val"}
        assert result.data == {"status": "done", "extra": 42}
        assert "context_patch" not in result.data
        assert "ok" not in result.data

    def test_dict_without_context_patch(self) -> None:
        raw = {"status": "ok", "value": 123}
        result = normalize_tool_result(raw)
        assert result.ok is True
        assert result.context_patch == {}
        assert result.data == {"status": "ok", "value": 123}

    def test_dict_with_ok_false(self) -> None:
        raw = {"ok": False, "error": "something failed"}
        result = normalize_tool_result(raw)
        assert result.ok is False
        assert result.data == {"error": "something failed"}

    def test_does_not_mutate_input(self) -> None:
        raw = {"x": 1, "context_patch": {"a": "b"}, "ok": True}
        normalize_tool_result(raw)
        assert "context_patch" in raw
        assert "ok" in raw
        assert raw["x"] == 1

    def test_empty_dict(self) -> None:
        result = normalize_tool_result({})
        assert result.ok is True
        assert result.data == {}
        assert result.context_patch == {}
