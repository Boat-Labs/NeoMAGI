"""Tests for P2-M2b Slice D: ReviewerExecutor + ReviewTool."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from src.procedures.deps import ProcedureActionDeps
from src.procedures.handoff import ReviewResult
from src.procedures.reviewer import ReviewerExecutor, ReviewTool, _parse_review
from src.procedures.roles import AgentRole
from src.procedures.types import ActiveProcedure
from src.tools.context import ToolContext


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeModelClient:
    def __init__(self, response: str) -> None:
        self._response = response

    async def chat(self, messages, model, temperature=None) -> str:
        return self._response


class _ErrorModelClient:
    async def chat(self, messages, model, temperature=None) -> str:
        raise RuntimeError("model error")


def _active_with_staging(handoff_id: str = "h1", worker_data: dict | None = None):
    ctx: dict[str, Any] = {}
    if worker_data is not None:
        ctx["_pending_handoffs"] = {handoff_id: worker_data}
    return ActiveProcedure(
        instance_id="inst-1",
        session_id="sess-1",
        spec_id="test.spec",
        spec_version=1,
        state="delegated",
        context=ctx,
        revision=1,
    )


def _make_deps(active: ActiveProcedure, response: str = '{"approved": true}'):
    return ProcedureActionDeps(
        active_procedure=active,
        spec=None,  # type: ignore[arg-type]
        model_client=_FakeModelClient(response),
        model="test",
    )


# ---------------------------------------------------------------------------
# _parse_review
# ---------------------------------------------------------------------------


class TestParseReview:
    def test_valid_json(self):
        r = _parse_review('{"approved": true, "concerns": ["c1"], "suggestions": [], "evidence": []}')
        assert r.approved is True
        assert r.concerns == ("c1",)

    def test_markdown_fenced(self):
        r = _parse_review('```json\n{"approved": false}\n```')
        assert r.approved is False

    def test_invalid_json_fail_closed(self):
        r = _parse_review("not json at all")
        assert r.approved is False
        assert "review_parse_failure" in r.concerns

    def test_non_object_fail_closed(self):
        r = _parse_review("[1, 2, 3]")
        assert r.approved is False


# ---------------------------------------------------------------------------
# ReviewerExecutor
# ---------------------------------------------------------------------------


class TestReviewerExecutor:
    @pytest.mark.asyncio
    async def test_approve(self):
        client = _FakeModelClient('{"approved": true, "concerns": [], "suggestions": ["good"]}')
        executor = ReviewerExecutor(client, model="test")
        result = await executor.review(
            work_product={"answer": 42},
            criteria=("correctness",),
        )
        assert result.approved is True
        assert result.suggestions == ("good",)

    @pytest.mark.asyncio
    async def test_reject(self):
        client = _FakeModelClient('{"approved": false, "concerns": ["wrong"]}')
        executor = ReviewerExecutor(client, model="test")
        result = await executor.review(
            work_product={"answer": -1},
            criteria=("correctness",),
        )
        assert result.approved is False
        assert "wrong" in result.concerns

    @pytest.mark.asyncio
    async def test_model_failure_fail_closed(self):
        executor = ReviewerExecutor(_ErrorModelClient(), model="test")
        result = await executor.review(
            work_product={},
            criteria=(),
        )
        assert result.approved is False
        assert "review_model_failure" in result.concerns


# ---------------------------------------------------------------------------
# ReviewTool
# ---------------------------------------------------------------------------


class TestReviewTool:
    @pytest.mark.asyncio
    async def test_no_deps_fail_closed(self):
        tool = ReviewTool()
        result = await tool.execute({"handoff_id": "h1"}, context=None)
        assert result["ok"] is False
        assert result["error_code"] == "REVIEW_NO_PROCEDURE_DEPS"

    @pytest.mark.asyncio
    async def test_handoff_not_found(self):
        tool = ReviewTool()
        active = _active_with_staging()  # no staging data
        deps = _make_deps(active)
        ctx = ToolContext(procedure_deps=deps)
        result = await tool.execute({"handoff_id": "missing"}, context=ctx)
        assert result["ok"] is False
        assert result["error_code"] == "REVIEW_HANDOFF_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_successful_review(self):
        tool = ReviewTool()
        active = _active_with_staging("h1", {"result": {"answer": 42}})
        deps = _make_deps(active, '{"approved": true, "concerns": []}')
        ctx = ToolContext(procedure_deps=deps)
        result = await tool.execute(
            {"handoff_id": "h1", "criteria": ["correctness"]},
            context=ctx,
        )
        assert result["ok"] is True
        assert result["approved"] is True
        # context_patch should have _review_results
        assert "h1" in result["context_patch"]["_review_results"]

    @pytest.mark.asyncio
    async def test_read_modify_write_preserves_existing(self):
        """Existing review results are preserved when adding new one."""
        tool = ReviewTool()
        active = ActiveProcedure(
            instance_id="inst-1",
            session_id="sess-1",
            spec_id="test.spec",
            spec_version=1,
            state="delegated",
            context={
                "_pending_handoffs": {"h2": {"result": {"x": 1}}},
                "_review_results": {"h1": {"approved": True}},
            },
            revision=1,
        )
        deps = _make_deps(active, '{"approved": false, "concerns": ["bad"]}')
        ctx = ToolContext(procedure_deps=deps)
        result = await tool.execute({"handoff_id": "h2"}, context=ctx)
        reviews = result["context_patch"]["_review_results"]
        assert "h1" in reviews  # preserved
        assert "h2" in reviews  # new

    def test_is_procedure_only(self):
        tool = ReviewTool()
        assert tool.is_procedure_only is True
        assert tool.allowed_modes == frozenset()
