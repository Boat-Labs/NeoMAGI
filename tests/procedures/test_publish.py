"""Tests for P2-M2b Slice F: PublishTool + merge_worker_result."""

from __future__ import annotations

from typing import Any

import pytest

from src.procedures.deps import ProcedureActionDeps
from src.procedures.publish import PublishTool, merge_worker_result
from src.procedures.roles import AgentRole
from src.procedures.types import ActiveProcedure
from src.tools.context import ToolContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _active_with_staging(
    handoffs: dict[str, dict] | None = None,
    reviews: dict[str, dict] | None = None,
) -> ActiveProcedure:
    ctx: dict[str, Any] = {}
    if handoffs is not None:
        ctx["_pending_handoffs"] = handoffs
    if reviews is not None:
        ctx["_review_results"] = reviews
    return ActiveProcedure(
        instance_id="inst-1",
        session_id="sess-1",
        spec_id="test.spec",
        spec_version=1,
        state="reviewed",
        context=ctx,
        revision=2,
    )


def _deps(active: ActiveProcedure) -> ProcedureActionDeps:
    return ProcedureActionDeps(
        active_procedure=active,
        spec=None,  # type: ignore[arg-type]
        model_client=None,
        model="test",
    )


# ---------------------------------------------------------------------------
# merge_worker_result
# ---------------------------------------------------------------------------


class TestMergeWorkerResult:
    def test_extracts_specified_keys(self):
        raw = {"result": {"answer": 42, "detail": "computed"}}
        patch = merge_worker_result(raw, ("answer",), {})
        assert patch == {"answer": 42}
        assert "detail" not in patch

    def test_missing_key_skipped(self):
        raw = {"result": {"answer": 42}}
        patch = merge_worker_result(raw, ("answer", "missing"), {})
        assert patch == {"answer": 42}

    def test_empty_merge_keys(self):
        raw = {"result": {"answer": 42}}
        patch = merge_worker_result(raw, (), {})
        assert patch == {}

    def test_prompt_compliant_worker_output(self):
        """WorkerResult.model_dump() stores inner result after extraction.

        Worker extracts the inner "result" dict, so staging has
        {"ok": true, "result": {"answer": 42}, ...}
        and merge_worker_result finds "answer" in raw["result"].
        """
        raw = {"ok": True, "result": {"answer": 42}, "evidence": [], "iterations_used": 1}
        patch = merge_worker_result(raw, ("answer",), {})
        assert patch == {"answer": 42}


# ---------------------------------------------------------------------------
# PublishTool
# ---------------------------------------------------------------------------


class TestPublishTool:
    def test_is_procedure_only(self):
        tool = PublishTool()
        assert tool.is_procedure_only is True
        assert tool.allowed_modes == frozenset()

    @pytest.mark.asyncio
    async def test_no_deps_fail_closed(self):
        tool = PublishTool()
        result = await tool.execute({"handoff_id": "h1"})
        assert result["ok"] is False
        assert result["error_code"] == "PUBLISH_NO_PROCEDURE_DEPS"

    @pytest.mark.asyncio
    async def test_role_denied_for_worker(self):
        tool = PublishTool()
        active = _active_with_staging({"h1": {"result": {}}})
        ctx = ToolContext(
            actor=AgentRole.worker,
            procedure_deps=_deps(active),
        )
        result = await tool.execute({"handoff_id": "h1"}, context=ctx)
        assert result["ok"] is False
        assert result["error_code"] == "PUBLISH_ROLE_DENIED"

    @pytest.mark.asyncio
    async def test_handoff_not_found(self):
        tool = PublishTool()
        active = _active_with_staging({})
        ctx = ToolContext(actor=AgentRole.primary, procedure_deps=_deps(active))
        result = await tool.execute({"handoff_id": "missing"}, context=ctx)
        assert result["ok"] is False
        assert result["error_code"] == "PUBLISH_HANDOFF_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_review_rejected(self):
        tool = PublishTool()
        active = _active_with_staging(
            handoffs={"h1": {"result": {"answer": 42}}},
            reviews={"h1": {"approved": False, "concerns": ["bad"]}},
        )
        ctx = ToolContext(actor=AgentRole.primary, procedure_deps=_deps(active))
        result = await tool.execute({"handoff_id": "h1", "merge_keys": ["answer"]}, context=ctx)
        assert result["ok"] is False
        assert result["error_code"] == "PUBLISH_REVIEW_REJECTED"

    @pytest.mark.asyncio
    async def test_successful_publish(self):
        tool = PublishTool()
        active = _active_with_staging(
            handoffs={"h1": {"result": {"answer": 42, "detail": "x"}}},
            reviews={"h1": {"approved": True}},
        )
        ctx = ToolContext(actor=AgentRole.primary, procedure_deps=_deps(active))
        result = await tool.execute(
            {"handoff_id": "h1", "merge_keys": ["answer"]},
            context=ctx,
        )
        assert result["ok"] is True
        assert result["published_keys"] == ["answer"]
        # context_patch: answer merged, staging cleaned
        patch = result["context_patch"]
        assert patch["answer"] == 42
        assert "h1" not in patch["_pending_handoffs"]
        assert "h1" not in patch["_review_results"]
        # D9: flush signal
        assert len(result["_publish_flush_texts"]) > 0

    @pytest.mark.asyncio
    async def test_publish_without_review(self):
        """Publish works when no review exists (review is optional)."""
        tool = PublishTool()
        active = _active_with_staging(handoffs={"h1": {"result": {"val": 1}}})
        ctx = ToolContext(actor=AgentRole.primary, procedure_deps=_deps(active))
        result = await tool.execute(
            {"handoff_id": "h1", "merge_keys": ["val"]},
            context=ctx,
        )
        assert result["ok"] is True
        assert result["context_patch"]["val"] == 1

    @pytest.mark.asyncio
    async def test_preserves_other_handoffs(self):
        tool = PublishTool()
        active = _active_with_staging(handoffs={
            "h1": {"result": {"a": 1}},
            "h2": {"result": {"b": 2}},
        })
        ctx = ToolContext(actor=AgentRole.primary, procedure_deps=_deps(active))
        result = await tool.execute(
            {"handoff_id": "h1", "merge_keys": ["a"]},
            context=ctx,
        )
        assert result["ok"] is True
        remaining = result["context_patch"]["_pending_handoffs"]
        assert "h1" not in remaining
        assert "h2" in remaining  # preserved
