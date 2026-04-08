"""End-to-end test fixture for P2-M2b Slice I: delegation → review → publish flow.

Uses mock model clients and fake tools to verify the full multi-agent
procedure lifecycle without hitting real LLM APIs or databases.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from src.procedures.delegation import DelegationTool
from src.procedures.deps import ProcedureActionDeps
from src.procedures.publish import PublishTool
from src.procedures.reviewer import ReviewTool
from src.procedures.roles import AgentRole
from src.procedures.types import ActiveProcedure
from src.tools.context import ToolContext
from src.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeChatMessage:
    content: str | None = None
    tool_calls: list[dict[str, str]] | None = None


class _FakeModelClient:
    """Model client that returns pre-configured responses for delegation + review."""

    def __init__(
        self,
        worker_answer: str = '{"answer": 42}',
        review_answer: str = '{"approved": true, "concerns": []}',
    ) -> None:
        self._worker_answer = worker_answer
        self._review_answer = review_answer

    async def chat_completion(self, messages, model, *, tools=None, temperature=None):
        return _FakeChatMessage(content=self._worker_answer)

    async def chat(self, messages, model, temperature=None):
        return self._review_answer


def _make_active(**ctx_overrides) -> ActiveProcedure:
    ctx: dict[str, Any] = {"key_a": "val_a"}
    ctx.update(ctx_overrides)
    return ActiveProcedure(
        instance_id="inst-1",
        session_id="sess-1",
        spec_id="test.delegation_flow",
        spec_version=1,
        state="planning",
        context=ctx,
        revision=0,
    )


def _make_deps(
    active: ActiveProcedure,
    client: _FakeModelClient | None = None,
) -> ProcedureActionDeps:
    return ProcedureActionDeps(
        active_procedure=active,
        spec=None,  # type: ignore[arg-type]
        model_client=client or _FakeModelClient(),
        model="test",
    )


def _primary_ctx(deps: ProcedureActionDeps) -> ToolContext:
    return ToolContext(
        scope_key="main",
        session_id="sess-1",
        actor=AgentRole.primary,
        procedure_deps=deps,
    )


# ---------------------------------------------------------------------------
# E2E: delegate → review → publish (happy path)
# ---------------------------------------------------------------------------


class TestDelegationFlowHappyPath:
    @pytest.mark.asyncio
    async def test_full_flow(self):
        """Primary delegates → worker executes → primary reviews → primary publishes."""
        reg = ToolRegistry()
        active = _make_active()
        client = _FakeModelClient(
            # Prompt-compliant nested response: worker extracts inner "result"
            worker_answer='{"result": {"answer": 42}, "evidence": ["computed"]}',
            review_answer='{"approved": true, "concerns": []}',
        )
        deps = _make_deps(active, client)
        ctx = _primary_ctx(deps)

        # 1. Delegate
        delegation_tool = DelegationTool(tool_registry=reg)
        d_result = await delegation_tool.execute(
            {"task_brief": "compute the answer", "include_keys": ["key_a"]},
            context=ctx,
        )
        assert d_result["ok"] is True
        handoff_id = d_result["handoff_id"]
        staging = d_result["context_patch"]["_pending_handoffs"]
        assert handoff_id in staging

        # 2. Simulate context update (ProcedureRuntime would do shallow merge)
        updated_ctx = {**active.context, **d_result["context_patch"]}
        active_after_delegate = ActiveProcedure(
            **{**active.model_dump(), "context": updated_ctx, "state": "delegated", "revision": 1}
        )
        deps2 = _make_deps(active_after_delegate, client)
        ctx2 = _primary_ctx(deps2)

        # 3. Review
        review_tool = ReviewTool()
        r_result = await review_tool.execute(
            {"handoff_id": handoff_id, "criteria": ["correctness"]},
            context=ctx2,
        )
        assert r_result["ok"] is True
        assert r_result["approved"] is True

        # 4. Simulate context update again
        updated_ctx2 = {**active_after_delegate.context, **r_result["context_patch"]}
        active_after_review = ActiveProcedure(
            **{**active_after_delegate.model_dump(), "context": updated_ctx2, "state": "reviewed", "revision": 2}
        )
        deps3 = _make_deps(active_after_review, client)
        ctx3 = _primary_ctx(deps3)

        # 5. Publish
        publish_tool = PublishTool()
        p_result = await publish_tool.execute(
            {"handoff_id": handoff_id, "merge_keys": ["answer"]},
            context=ctx3,
        )
        assert p_result["ok"] is True
        assert p_result["published_keys"] == ["answer"]
        assert p_result["context_patch"]["answer"] == 42
        # Staging cleaned
        assert handoff_id not in p_result["context_patch"]["_pending_handoffs"]
        # D9: flush signal
        assert len(p_result.get("_publish_flush_texts", [])) > 0


# ---------------------------------------------------------------------------
# E2E: delegate → worker fails → stays in state
# ---------------------------------------------------------------------------


class TestDelegationFlowWorkerFailure:
    @pytest.mark.asyncio
    async def test_worker_failure_no_staging_model_retries(self):
        """When worker fails, delegation returns ok=False without staging.

        M2a's apply_action ignores context_patch on ok=False, so failed
        results are not staged. The model sees the error and can re-invoke
        the delegate action to retry.
        """
        reg = ToolRegistry()

        class _FailModelClient:
            async def chat_completion(self, *args, **kwargs):
                raise TimeoutError("timeout")

            async def chat(self, *args, **kwargs):
                return '{"approved": false}'

        active = _make_active()
        deps = _make_deps(active, _FailModelClient())  # type: ignore[arg-type]
        ctx = _primary_ctx(deps)

        delegation_tool = DelegationTool(tool_registry=reg)
        d_result = await delegation_tool.execute(
            {"task_brief": "compute something"},
            context=ctx,
        )
        # Worker failed → delegation reports failure, no staging
        assert d_result["ok"] is False
        assert d_result["error_code"] == "WORKER_MODEL_TIMEOUT"
        assert "context_patch" not in d_result  # no staging on failure


# ---------------------------------------------------------------------------
# E2E: delegate → review rejects → revise → re-delegate → publish
# ---------------------------------------------------------------------------


class TestDelegationFlowReviewReject:
    @pytest.mark.asyncio
    async def test_review_reject_blocks_publish(self):
        """Review rejection prevents publish."""
        reg = ToolRegistry()
        client = _FakeModelClient(
            worker_answer='{"answer": -1}',
            review_answer='{"approved": false, "concerns": ["wrong answer"]}',
        )
        active = _make_active()
        deps = _make_deps(active, client)
        ctx = _primary_ctx(deps)

        # 1. Delegate
        delegation_tool = DelegationTool(tool_registry=reg)
        d_result = await delegation_tool.execute(
            {"task_brief": "compute"}, context=ctx,
        )
        handoff_id = d_result["handoff_id"]

        # 2. Update context
        updated_ctx = {**active.context, **d_result["context_patch"]}
        active2 = ActiveProcedure(
            **{**active.model_dump(), "context": updated_ctx, "revision": 1}
        )
        deps2 = _make_deps(active2, client)
        ctx2 = _primary_ctx(deps2)

        # 3. Review (rejects)
        review_tool = ReviewTool()
        r_result = await review_tool.execute(
            {"handoff_id": handoff_id}, context=ctx2,
        )
        assert r_result["approved"] is False

        # 4. Update context with review
        updated_ctx2 = {**active2.context, **r_result["context_patch"]}
        active3 = ActiveProcedure(
            **{**active2.model_dump(), "context": updated_ctx2, "revision": 2}
        )
        deps3 = _make_deps(active3, client)
        ctx3 = _primary_ctx(deps3)

        # 5. Attempt publish — should be denied (review rejected)
        publish_tool = PublishTool()
        p_result = await publish_tool.execute(
            {"handoff_id": handoff_id, "merge_keys": ["answer"]},
            context=ctx3,
        )
        assert p_result["ok"] is False
        assert p_result["error_code"] == "PUBLISH_REVIEW_REJECTED"


# ---------------------------------------------------------------------------
# Role guard: worker cannot publish
# ---------------------------------------------------------------------------


class TestRoleGuards:
    @pytest.mark.asyncio
    async def test_worker_cannot_publish(self):
        active = _make_active(_pending_handoffs={"h1": {"result": {"x": 1}}})
        deps = _make_deps(active)
        ctx = ToolContext(
            actor=AgentRole.worker,
            procedure_deps=deps,
        )
        publish_tool = PublishTool()
        result = await publish_tool.execute(
            {"handoff_id": "h1", "merge_keys": ["x"]}, context=ctx,
        )
        assert result["ok"] is False
        assert result["error_code"] == "PUBLISH_ROLE_DENIED"
