"""Tests for src/procedures/runtime.py — ProcedureRuntime core executor.

Uses an in-memory mock store to test runtime logic without PostgreSQL.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from src.procedures.registry import (
    ProcedureContextRegistry,
    ProcedureGuardRegistry,
    ProcedureSpecRegistry,
)
from src.procedures.runtime import (
    PROCEDURE_ACTION_DENIED,
    PROCEDURE_CAS_CONFLICT,
    PROCEDURE_CONFLICT,
    PROCEDURE_INVALID_ARGS,
    PROCEDURE_INVALID_PATCH,
    PROCEDURE_TOOL_UNAVAILABLE,
    PROCEDURE_UNKNOWN,
    ProcedureRuntime,
)
from src.procedures.types import (
    ActionSpec,
    ActiveProcedure,
    CasConflict,
    GuardDecision,
    ProcedureExecutionMetadata,
    ProcedureSpec,
    StateSpec,
)
from src.tools.base import BaseTool, ToolMode
from src.tools.context import ToolContext
from src.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------


class _CheckpointContext(BaseModel):
    value: str = ""
    counter: int = 0


class _FakeAsyncTool(BaseTool):
    """Fake tool for testing procedure runtime."""

    def __init__(
        self,
        name: str = "fake_tool",
        result: dict | None = None,
        raise_error: bool = False,
    ) -> None:
        self._name = name
        self._result = result or {"status": "done"}
        self._raise_error = raise_error
        self.call_count = 0
        self.last_args: dict = {}

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Fake tool {self._name}"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.coding})

    async def execute(self, arguments: dict, context: ToolContext | None = None) -> dict:
        self.call_count += 1
        self.last_args = arguments
        if self._raise_error:
            raise RuntimeError("Tool exploded")
        return dict(self._result)


class _MockStore:
    """In-memory mock of ProcedureStore for unit testing."""

    def __init__(self) -> None:
        self._instances: dict[str, ActiveProcedure] = {}
        self._completed: set[str] = set()

    async def create(self, active: ActiveProcedure) -> ActiveProcedure:
        # Enforce single-active per session
        for inst in self._instances.values():
            if inst.session_id == active.session_id and inst.instance_id not in self._completed:
                raise Exception("Single-active constraint violated")
        self._instances[active.instance_id] = active
        return active

    async def get_active(self, session_id: str) -> ActiveProcedure | None:
        for inst in self._instances.values():
            if inst.session_id == session_id and inst.instance_id not in self._completed:
                return inst
        return None

    async def get(self, instance_id: str) -> ActiveProcedure | None:
        return self._instances.get(instance_id)

    async def cas_update(
        self,
        instance_id: str,
        expected_revision: int,
        *,
        state: str,
        context: dict[str, Any],
        completed_at: bool = False,
    ) -> ActiveProcedure | CasConflict:
        inst = self._instances.get(instance_id)
        if inst is None or inst.instance_id in self._completed:
            return CasConflict(instance_id=instance_id, expected_revision=expected_revision)
        if inst.revision != expected_revision:
            return CasConflict(
                instance_id=instance_id,
                expected_revision=expected_revision,
                actual_revision=inst.revision,
            )
        updated = ActiveProcedure(
            instance_id=inst.instance_id,
            session_id=inst.session_id,
            spec_id=inst.spec_id,
            spec_version=inst.spec_version,
            state=state,
            context=context,
            execution_metadata=inst.execution_metadata,
            revision=expected_revision + 1,
        )
        self._instances[instance_id] = updated
        if completed_at:
            self._completed.add(instance_id)
        return updated


def _build_runtime(
    tool_result: dict | None = None,
    tool_raises: bool = False,
    enter_guard_allows: bool = True,
    action_guard_allows: bool = True,
    with_enter_guard: bool = False,
    with_action_guard: bool = False,
) -> tuple[ProcedureRuntime, _MockStore, _FakeAsyncTool]:
    """Build a minimal ProcedureRuntime with test fixtures."""
    tool = _FakeAsyncTool(result=tool_result, raise_error=tool_raises)
    tool_reg = ToolRegistry()
    tool_reg.register(tool)

    ctx_reg = ProcedureContextRegistry()
    ctx_reg.register("CheckpointCtx", _CheckpointContext)

    guard_reg = ProcedureGuardRegistry()

    def _enter_guard(ctx, tc=None):
        return GuardDecision(
            allowed=enter_guard_allows,
            code="" if enter_guard_allows else "ENTRY_DENIED",
            detail="" if enter_guard_allows else "Entry denied by guard",
        )

    def _action_guard(active, args, tc=None):
        return GuardDecision(
            allowed=action_guard_allows,
            code="" if action_guard_allows else "ACTION_DENIED",
            detail="" if action_guard_allows else "Action denied by guard",
        )

    guard_reg.register("entry_check", _enter_guard)
    guard_reg.register("action_check", _action_guard)

    spec = ProcedureSpec(
        id="test.checkpoint",
        version=1,
        summary="Test checkpoint procedure",
        entry_policy="explicit",
        allowed_modes=frozenset({ToolMode.coding}),
        context_model="CheckpointCtx",
        initial_state="draft",
        states={
            "draft": StateSpec(actions={
                "submit": ActionSpec(
                    tool="fake_tool",
                    to="review",
                    guard="action_check" if with_action_guard else None,
                ),
            }),
            "review": StateSpec(actions={
                "confirm": ActionSpec(tool="fake_tool", to="done"),
            }),
            "done": StateSpec(),
        },
        enter_guard="entry_check" if with_enter_guard else None,
    )

    spec_reg = ProcedureSpecRegistry()
    spec_reg.register(spec)

    store = _MockStore()
    runtime = ProcedureRuntime(spec_reg, ctx_reg, guard_reg, store, tool_reg)
    return runtime, store, tool


# ---------------------------------------------------------------------------
# enter_procedure tests
# ---------------------------------------------------------------------------


class TestEnterProcedure:
    @pytest.mark.asyncio
    async def test_enter_success(self) -> None:
        runtime, store, _ = _build_runtime()
        result = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(result, ActiveProcedure)
        assert result.state == "draft"
        assert result.revision == 0
        assert result.session_id == "s1"

    @pytest.mark.asyncio
    async def test_enter_with_initial_context(self) -> None:
        runtime, _, _ = _build_runtime()
        result = await runtime.enter_procedure(
            "s1", "test.checkpoint", initial_context={"value": "hello"},
        )
        assert isinstance(result, ActiveProcedure)
        assert result.context["value"] == "hello"

    @pytest.mark.asyncio
    async def test_enter_unknown_spec(self) -> None:
        runtime, _, _ = _build_runtime()
        result = await runtime.enter_procedure("s1", "nonexistent")
        assert isinstance(result, dict)
        assert result["error_code"] == PROCEDURE_UNKNOWN

    @pytest.mark.asyncio
    async def test_enter_duplicate_conflict(self) -> None:
        runtime, _, _ = _build_runtime()
        r1 = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(r1, ActiveProcedure)
        r2 = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(r2, dict)
        assert r2["error_code"] == PROCEDURE_CONFLICT

    @pytest.mark.asyncio
    async def test_enter_wrong_mode(self) -> None:
        runtime, _, _ = _build_runtime()
        result = await runtime.enter_procedure(
            "s1", "test.checkpoint", mode=ToolMode.chat_safe,
        )
        assert isinstance(result, dict)
        assert result["error_code"] == PROCEDURE_ACTION_DENIED

    @pytest.mark.asyncio
    async def test_enter_invalid_context(self) -> None:
        runtime, _, _ = _build_runtime()
        result = await runtime.enter_procedure(
            "s1", "test.checkpoint",
            initial_context={"counter": "not_an_int"},
        )
        assert isinstance(result, dict)
        assert result["error_code"] == PROCEDURE_INVALID_PATCH

    @pytest.mark.asyncio
    async def test_enter_guard_deny(self) -> None:
        runtime, _, _ = _build_runtime(with_enter_guard=True, enter_guard_allows=False)
        result = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(result, dict)
        assert result["error_code"] == PROCEDURE_ACTION_DENIED

    @pytest.mark.asyncio
    async def test_enter_guard_allow(self) -> None:
        runtime, _, _ = _build_runtime(with_enter_guard=True, enter_guard_allows=True)
        result = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(result, ActiveProcedure)

    @pytest.mark.asyncio
    async def test_enter_with_execution_metadata(self) -> None:
        runtime, _, _ = _build_runtime()
        meta = ProcedureExecutionMetadata(actor="user-1")
        result = await runtime.enter_procedure(
            "s1", "test.checkpoint", execution_metadata=meta,
        )
        assert isinstance(result, ActiveProcedure)
        assert result.execution_metadata.actor == "user-1"


# ---------------------------------------------------------------------------
# apply_action tests
# ---------------------------------------------------------------------------


class TestApplyAction:
    @pytest.mark.asyncio
    async def test_action_success_transitions(self) -> None:
        runtime, store, tool = _build_runtime(
            tool_result={"status": "submitted", "context_patch": {"value": "submitted"}},
        )
        active = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(active, ActiveProcedure)

        result = await runtime.apply_action(
            active.instance_id, "submit", "{}", 0,
            mode=ToolMode.coding,
        )
        assert result["ok"] is True
        assert result["from_state"] == "draft"
        assert result["to_state"] == "review"
        assert result["revision"] == 1
        assert tool.call_count == 1

    @pytest.mark.asyncio
    async def test_action_to_terminal_completes(self) -> None:
        runtime, store, tool = _build_runtime()
        active = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(active, ActiveProcedure)

        # draft -> review
        r1 = await runtime.apply_action(active.instance_id, "submit", "{}", 0)
        assert r1["ok"] is True

        # review -> done (terminal)
        r2 = await runtime.apply_action(active.instance_id, "confirm", "{}", 1)
        assert r2["ok"] is True
        assert r2["completed"] is True

        # Session should now allow new procedure
        new = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(new, ActiveProcedure)

    @pytest.mark.asyncio
    async def test_action_unknown_instance(self) -> None:
        runtime, _, _ = _build_runtime()
        result = await runtime.apply_action("nonexistent", "submit", "{}", 0)
        assert result["error_code"] == PROCEDURE_UNKNOWN

    @pytest.mark.asyncio
    async def test_action_wrong_revision(self) -> None:
        runtime, _, _ = _build_runtime()
        active = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(active, ActiveProcedure)

        result = await runtime.apply_action(active.instance_id, "submit", "{}", 99)
        assert result["error_code"] == PROCEDURE_CAS_CONFLICT
        assert result["retryable"] is True

    @pytest.mark.asyncio
    async def test_action_not_allowed_in_state(self) -> None:
        runtime, _, _ = _build_runtime()
        active = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(active, ActiveProcedure)

        result = await runtime.apply_action(active.instance_id, "confirm", "{}", 0)
        assert result["error_code"] == PROCEDURE_ACTION_DENIED

    @pytest.mark.asyncio
    async def test_action_tool_failure_stays(self) -> None:
        runtime, _, tool = _build_runtime(
            tool_result={"ok": False, "error": "validation failed"},
        )
        active = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(active, ActiveProcedure)

        result = await runtime.apply_action(active.instance_id, "submit", "{}", 0)
        assert result["ok"] is False
        assert result["state"] == "draft"
        assert result["revision"] == 0

    @pytest.mark.asyncio
    async def test_action_tool_exception_stays(self) -> None:
        runtime, _, _ = _build_runtime(tool_raises=True)
        active = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(active, ActiveProcedure)

        result = await runtime.apply_action(active.instance_id, "submit", "{}", 0)
        assert result["error_code"] == PROCEDURE_TOOL_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_action_guard_deny(self) -> None:
        runtime, _, _ = _build_runtime(
            with_action_guard=True,
            action_guard_allows=False,
        )
        active = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(active, ActiveProcedure)

        result = await runtime.apply_action(active.instance_id, "submit", "{}", 0)
        assert result["error_code"] == PROCEDURE_ACTION_DENIED

    @pytest.mark.asyncio
    async def test_action_context_patch_merges(self) -> None:
        runtime, store, _ = _build_runtime(
            tool_result={"context_patch": {"value": "updated", "counter": 1}},
        )
        active = await runtime.enter_procedure(
            "s1", "test.checkpoint", initial_context={"value": "initial"},
        )
        assert isinstance(active, ActiveProcedure)

        result = await runtime.apply_action(active.instance_id, "submit", "{}", 0)
        assert result["ok"] is True

        # Check merged context
        updated = await store.get(active.instance_id)
        assert updated is not None
        assert updated.context["value"] == "updated"
        assert updated.context["counter"] == 1

    @pytest.mark.asyncio
    async def test_action_invalid_context_patch(self) -> None:
        runtime, _, _ = _build_runtime(
            tool_result={"context_patch": {"counter": "not_int"}},
        )
        active = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(active, ActiveProcedure)

        result = await runtime.apply_action(active.instance_id, "submit", "{}", 0)
        assert result["error_code"] == PROCEDURE_INVALID_PATCH

    @pytest.mark.asyncio
    async def test_action_malformed_context_patch_type(self) -> None:
        """Tool returning context_patch as non-dict should return structured error."""
        runtime, _, _ = _build_runtime(
            tool_result={"context_patch": "not_a_dict"},
        )
        active = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(active, ActiveProcedure)

        result = await runtime.apply_action(active.instance_id, "submit", "{}", 0)
        assert result["ok"] is False
        # Should be a structured error, not a Python exception
        assert result["error_code"] in (PROCEDURE_INVALID_PATCH,)

    @pytest.mark.asyncio
    async def test_sequential_actions_revision_bump(self) -> None:
        """Same turn: after action 1 succeeds, action 2 must use bumped revision."""
        runtime, store, _ = _build_runtime(
            tool_result={"context_patch": {"value": "step1"}},
        )
        active = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(active, ActiveProcedure)

        r1 = await runtime.apply_action(active.instance_id, "submit", "{}", 0)
        assert r1["ok"] is True
        assert r1["revision"] == 1

        # Now try confirm with revision 1 (correct)
        r2 = await runtime.apply_action(active.instance_id, "confirm", "{}", 1)
        assert r2["ok"] is True
        assert r2["revision"] == 2

    @pytest.mark.asyncio
    async def test_failed_action_allows_retry_same_revision(self) -> None:
        """When tool returns ok=False, revision doesn't change; retry is possible."""
        tool = _FakeAsyncTool(result={"ok": False, "error": "temporary"})
        tool_reg = ToolRegistry()
        tool_reg.register(tool)
        ctx_reg = ProcedureContextRegistry()
        ctx_reg.register("CheckpointCtx", _CheckpointContext)
        guard_reg = ProcedureGuardRegistry()
        spec = ProcedureSpec(
            id="retry_test",
            version=1,
            summary="Retry test",
            entry_policy="explicit",
            allowed_modes=frozenset({ToolMode.coding}),
            context_model="CheckpointCtx",
            initial_state="draft",
            states={
                "draft": StateSpec(actions={
                    "submit": ActionSpec(tool="fake_tool", to="done"),
                }),
                "done": StateSpec(),
            },
        )
        spec_reg = ProcedureSpecRegistry()
        spec_reg.register(spec)
        store = _MockStore()
        runtime = ProcedureRuntime(spec_reg, ctx_reg, guard_reg, store, tool_reg)

        active = await runtime.enter_procedure("s1", "retry_test")
        assert isinstance(active, ActiveProcedure)

        # First attempt fails
        r1 = await runtime.apply_action(active.instance_id, "submit", "{}", 0)
        assert r1["ok"] is False
        assert r1["revision"] == 0

        # Fix the tool and retry with same revision
        tool._result = {"status": "fixed"}
        r2 = await runtime.apply_action(active.instance_id, "submit", "{}", 0)
        assert r2["ok"] is True
        assert r2["revision"] == 1

    @pytest.mark.asyncio
    async def test_action_invalid_json_args_rejected(self) -> None:
        """Malformed JSON args must be rejected, not silently converted to {}."""
        runtime, _, tool = _build_runtime()
        active = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(active, ActiveProcedure)

        result = await runtime.apply_action(active.instance_id, "submit", "not json", 0)
        assert result["ok"] is False
        assert result["error_code"] == PROCEDURE_INVALID_ARGS
        assert tool.call_count == 0  # tool must NOT have been called

    @pytest.mark.asyncio
    async def test_action_non_dict_args_rejected(self) -> None:
        """Non-dict args (e.g. a list) must be rejected."""
        runtime, _, tool = _build_runtime()
        active = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(active, ActiveProcedure)

        result = await runtime.apply_action(active.instance_id, "submit", "[1,2]", 0)
        assert result["ok"] is False
        assert result["error_code"] == PROCEDURE_INVALID_ARGS
        assert tool.call_count == 0


# ---------------------------------------------------------------------------
# Async guard tests
# ---------------------------------------------------------------------------


class TestAsyncGuard:
    @pytest.mark.asyncio
    async def test_async_enter_guard(self) -> None:
        tool = _FakeAsyncTool()
        tool_reg = ToolRegistry()
        tool_reg.register(tool)
        ctx_reg = ProcedureContextRegistry()
        ctx_reg.register("CheckpointCtx", _CheckpointContext)
        guard_reg = ProcedureGuardRegistry()

        async def async_guard(ctx, tc=None):
            return GuardDecision(allowed=True)

        guard_reg.register("async_check", async_guard)

        spec = ProcedureSpec(
            id="async_test",
            version=1,
            summary="Async guard test",
            entry_policy="explicit",
            allowed_modes=frozenset({ToolMode.coding}),
            context_model="CheckpointCtx",
            initial_state="start",
            enter_guard="async_check",
            states={"start": StateSpec()},
        )
        spec_reg = ProcedureSpecRegistry()
        spec_reg.register(spec)
        store = _MockStore()
        runtime = ProcedureRuntime(spec_reg, ctx_reg, guard_reg, store, tool_reg)

        result = await runtime.enter_procedure("s1", "async_test")
        assert isinstance(result, ActiveProcedure)


# ---------------------------------------------------------------------------
# load_active tests
# ---------------------------------------------------------------------------


class TestLoadActive:
    @pytest.mark.asyncio
    async def test_load_active_returns_none_when_empty(self) -> None:
        runtime, _, _ = _build_runtime()
        result = await runtime.load_active("s1")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_active_returns_procedure(self) -> None:
        runtime, _, _ = _build_runtime()
        entered = await runtime.enter_procedure("s1", "test.checkpoint")
        assert isinstance(entered, ActiveProcedure)

        loaded = await runtime.load_active("s1")
        assert loaded is not None
        assert loaded.instance_id == entered.instance_id
