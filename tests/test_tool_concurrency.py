"""Tests for P2-M1 Post Works P2: Tool Concurrency Metadata.

Covers:
- Slice A: BaseTool default fail-closed metadata + V1 explicit overrides
- Slice B: _build_execution_groups grouping logic (incl. start_index)
- Slice C: Parallel execution overlap + deterministic event/transcript order
- Slice D: Observability log events
- ToolCallInfo pre-execution timing guarantee
- Regression: mode denied, unknown tool, guardrail denied semantics unchanged
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.events import ToolCallInfo
from src.agent.guardrail import GuardCheckResult
from src.agent.message_flow import _handle_tool_calls
from src.agent.tool_concurrency import (
    _MAX_PARALLEL_TOOLS,
    _build_execution_groups,
    _execute_group,
    _execute_parallel,
    _ExecutionGroup,
    _is_parallel_eligible,
    _run_single_tool,
)
from src.tools.base import BaseTool, RiskLevel, ToolGroup, ToolMode
from src.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _StubTool(BaseTool):
    """Minimal tool with configurable concurrency metadata."""

    def __init__(
        self,
        name: str,
        *,
        read_only: bool = False,
        concurrency_safe: bool = False,
        delay: float = 0.0,
    ) -> None:
        self._name = name
        self._read_only = read_only
        self._concurrency_safe = concurrency_safe
        self._delay = delay

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"Stub tool {self._name}"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def group(self) -> ToolGroup:
        return ToolGroup.world

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.chat_safe, ToolMode.coding})

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.low

    @property
    def is_read_only(self) -> bool:
        return self._read_only

    @property
    def is_concurrency_safe(self) -> bool:
        return self._concurrency_safe

    async def execute(self, arguments: dict, context: Any = None) -> dict:
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        return {"ok": True, "tool": self._name}


class _CodingOnlyStubTool(_StubTool):
    """Tool available ONLY in coding mode (for mode-denial tests)."""

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.coding})


def _tc(name: str, args: str = "{}", call_id: str | None = None) -> dict[str, str]:
    """Shorthand for a tool call dict."""
    return {
        "name": name,
        "id": call_id or f"call_{name}",
        "arguments": args,
    }


def _make_registry(*tools: BaseTool) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


def _make_loop(registry: ToolRegistry | None = None) -> MagicMock:
    """Build a minimal AgentLoop mock for tool execution tests."""
    loop = MagicMock()
    loop._tool_registry = registry

    async def _exec_tool(name, args_json, *, scope_key, session_id, guard_state, principal_id=None):
        if registry is None:
            return {"error_code": "NO_REGISTRY", "message": "no registry"}
        tool = registry.get(name)
        if tool is None:
            return {"error_code": "UNKNOWN_TOOL", "message": f"Unknown tool: {name}"}
        args = json.loads(args_json) if isinstance(args_json, str) else args_json
        return await tool.execute(args)

    loop._execute_tool = AsyncMock(side_effect=_exec_tool)
    loop._session_manager = MagicMock()
    loop._session_manager.append_message = AsyncMock()
    return loop


def _make_state(session_id: str = "test-session") -> MagicMock:
    state = MagicMock()
    state.session_id = session_id
    state.scope_key = "main"
    state.lock_token = "tok"
    state.mode = ToolMode.chat_safe
    state.accumulated_failure_signals = []
    state.procedure_action_map = {}
    state.write_tool_counts = {}
    return state


def _passed_guard() -> GuardCheckResult:
    return GuardCheckResult(passed=True)


# ===========================================================================
# Slice A: Metadata defaults + V1 overrides
# ===========================================================================


class TestMetadataDefaults:
    """BaseTool default fail-closed: is_read_only=False, is_concurrency_safe=False."""

    def test_bare_tool_defaults_false(self):
        tool = _StubTool("bare")
        assert tool.is_read_only is False
        assert tool.is_concurrency_safe is False

    def test_explicit_read_only_override(self):
        tool = _StubTool("ro", read_only=True)
        assert tool.is_read_only is True
        assert tool.is_concurrency_safe is False

    def test_explicit_both_override(self):
        tool = _StubTool("par", read_only=True, concurrency_safe=True)
        assert tool.is_read_only is True
        assert tool.is_concurrency_safe is True


class TestV1ToolMarking:
    """V1 builtin tools have correct concurrency metadata."""

    def test_current_time(self):
        from src.tools.builtins.current_time import CurrentTimeTool

        t = CurrentTimeTool()
        assert t.is_read_only is True
        assert t.is_concurrency_safe is True

    def test_memory_search(self):
        from src.tools.builtins.memory_search import MemorySearchTool

        t = MemorySearchTool()
        assert t.is_read_only is True
        assert t.is_concurrency_safe is True

    def test_soul_status(self):
        from src.tools.builtins.soul_status import SoulStatusTool

        t = SoulStatusTool()
        assert t.is_read_only is True
        assert t.is_concurrency_safe is True

    def test_read_file_read_only_but_not_concurrency_safe(self):
        from pathlib import Path

        from src.tools.builtins.read_file import ReadFileTool

        t = ReadFileTool(Path("/tmp"))
        assert t.is_read_only is True
        assert t.is_concurrency_safe is False

    def test_memory_append_default(self):
        from src.tools.builtins.memory_append import MemoryAppendTool

        t = MemoryAppendTool(writer=None)  # type: ignore[arg-type]
        assert t.is_read_only is False
        assert t.is_concurrency_safe is False

    def test_soul_propose_default(self):
        from src.tools.builtins.soul_propose import SoulProposeTool

        t = SoulProposeTool()
        assert t.is_read_only is False
        assert t.is_concurrency_safe is False

    def test_soul_rollback_default(self):
        from src.tools.builtins.soul_rollback import SoulRollbackTool

        t = SoulRollbackTool()
        assert t.is_read_only is False
        assert t.is_concurrency_safe is False


# ===========================================================================
# Slice B: Execution group building
# ===========================================================================


class TestBuildExecutionGroups:
    """_build_execution_groups correctly splits tool calls into groups."""

    def _par(self, name: str) -> _StubTool:
        return _StubTool(name, read_only=True, concurrency_safe=True)

    def _barrier(self, name: str) -> _StubTool:
        return _StubTool(name)

    def test_all_parallel_single_group(self):
        reg = _make_registry(self._par("a"), self._par("b"), self._par("c"))
        groups = _build_execution_groups([_tc("a"), _tc("b"), _tc("c")], reg)
        assert len(groups) == 1
        assert groups[0].parallel is True
        assert len(groups[0].tool_calls) == 3
        assert groups[0].start_index == 0

    def test_single_barrier(self):
        reg = _make_registry(self._barrier("w"))
        groups = _build_execution_groups([_tc("w")], reg)
        assert len(groups) == 1
        assert groups[0].parallel is False
        assert groups[0].start_index == 0

    def test_parallel_then_barrier_then_parallel(self):
        """[a, b, w, c] -> par[a,b], barrier[w], par[c]"""
        reg = _make_registry(
            self._par("a"), self._par("b"), self._barrier("w"), self._par("c"),
        )
        groups = _build_execution_groups(
            [_tc("a"), _tc("b"), _tc("w"), _tc("c")], reg,
        )
        assert len(groups) == 3
        assert groups[0].parallel is True
        assert [t["name"] for t in groups[0].tool_calls] == ["a", "b"]
        assert groups[0].start_index == 0
        assert groups[1].parallel is False
        assert groups[1].tool_calls[0]["name"] == "w"
        assert groups[1].start_index == 2
        assert groups[2].parallel is True
        assert groups[2].tool_calls[0]["name"] == "c"
        assert groups[2].start_index == 3

    def test_unknown_tool_is_barrier(self):
        """Tool not in registry -> barrier."""
        reg = _make_registry(self._par("a"))
        groups = _build_execution_groups([_tc("unknown"), _tc("a")], reg)
        assert len(groups) == 2
        assert groups[0].parallel is False
        assert groups[0].start_index == 0
        assert groups[1].parallel is True
        assert groups[1].start_index == 1

    def test_read_only_but_not_concurrency_safe_is_barrier(self):
        """read_file: is_read_only=True, is_concurrency_safe=False -> barrier."""
        ro = _StubTool("read_file", read_only=True, concurrency_safe=False)
        par = self._par("a")
        reg = _make_registry(ro, par)
        groups = _build_execution_groups([_tc("read_file"), _tc("a")], reg)
        assert len(groups) == 2
        assert groups[0].parallel is False
        assert groups[1].parallel is True

    def test_no_registry_all_barriers(self):
        groups = _build_execution_groups([_tc("a"), _tc("b")], None)
        assert len(groups) == 2
        assert all(not g.parallel for g in groups)

    def test_empty_tool_calls(self):
        groups = _build_execution_groups([], _make_registry())
        assert groups == []


class TestIsParallelEligible:
    def test_both_true(self):
        reg = _make_registry(_StubTool("x", read_only=True, concurrency_safe=True))
        assert _is_parallel_eligible("x", reg) is True

    def test_only_read_only(self):
        reg = _make_registry(_StubTool("x", read_only=True))
        assert _is_parallel_eligible("x", reg) is False

    def test_only_concurrency_safe(self):
        reg = _make_registry(_StubTool("x", concurrency_safe=True))
        assert _is_parallel_eligible("x", reg) is False

    def test_unknown(self):
        reg = _make_registry()
        assert _is_parallel_eligible("missing", reg) is False

    def test_no_registry(self):
        assert _is_parallel_eligible("x", None) is False


# ===========================================================================
# Slice C: Parallel execution + deterministic ordering
# ===========================================================================


class TestRunSingleTool:
    """_run_single_tool returns _ToolOutcome without side effects."""

    @pytest.mark.asyncio
    async def test_success(self):
        tool = _StubTool("ok", read_only=True, concurrency_safe=True)
        reg = _make_registry(tool)
        loop = _make_loop(reg)
        state = _make_state()
        outcome = await _run_single_tool(loop, state, _tc("ok"), _passed_guard())
        assert outcome.result == {"ok": True, "tool": "ok"}
        assert outcome.denied_event is None
        assert outcome.failure_signal is None

    @pytest.mark.asyncio
    async def test_mode_denied(self):
        """Tool not available in current mode -> denied outcome."""
        tool = _CodingOnlyStubTool("coding_only")
        reg = _make_registry(tool)
        loop = _make_loop(reg)
        state = _make_state()
        state.mode = ToolMode.chat_safe
        outcome = await _run_single_tool(loop, state, _tc("coding_only"), _passed_guard())
        assert outcome.denied_event is not None
        assert outcome.failure_signal == "guard_denied:coding_only"


class TestParallelExecution:
    """Parallel groups execute concurrently but emit in original order."""

    @pytest.mark.asyncio
    async def test_concurrent_overlap(self):
        """Two slow tools should overlap in time when executed in parallel."""
        t1 = _StubTool("s1", read_only=True, concurrency_safe=True, delay=0.1)
        t2 = _StubTool("s2", read_only=True, concurrency_safe=True, delay=0.1)
        reg = _make_registry(t1, t2)
        loop = _make_loop(reg)
        state = _make_state()
        guard = _passed_guard()
        group = _ExecutionGroup(
            tool_calls=[_tc("s1"), _tc("s2")], parallel=True, start_index=0,
        )

        start = time.monotonic()
        outcomes = await _execute_parallel(
            loop, state, group, guard,
            session_id="test-session", iteration=0,
        )
        elapsed = time.monotonic() - start

        assert len(outcomes) == 2
        assert outcomes[0].result["tool"] == "s1"
        assert outcomes[1].result["tool"] == "s2"
        # Parallel: should complete in ~0.1s, not ~0.2s
        assert elapsed < 0.18, f"Expected parallel overlap, took {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_bounded_parallelism(self):
        """Groups larger than _MAX_PARALLEL_TOOLS are split into batches."""
        tools = [
            _StubTool(f"t{i}", read_only=True, concurrency_safe=True)
            for i in range(_MAX_PARALLEL_TOOLS + 2)
        ]
        reg = _make_registry(*tools)
        loop = _make_loop(reg)
        state = _make_state()
        guard = _passed_guard()
        tcs = [_tc(f"t{i}") for i in range(len(tools))]
        group = _ExecutionGroup(tool_calls=tcs, parallel=True, start_index=0)

        outcomes = await _execute_parallel(
            loop, state, group, guard,
            session_id="test-session", iteration=0,
        )
        assert len(outcomes) == len(tools)
        for i, o in enumerate(outcomes):
            assert o.result["tool"] == f"t{i}"


class TestHandleToolCallsEventTiming:
    """ToolCallInfo is yielded BEFORE tool execution, ToolDenied after."""

    @pytest.mark.asyncio
    async def test_tool_call_info_before_execution(self):
        """ToolCallInfo events appear before tool results are appended."""
        t1 = _StubTool("a", read_only=True, concurrency_safe=True, delay=0.05)
        t2 = _StubTool("b", read_only=True, concurrency_safe=True, delay=0.05)
        reg = _make_registry(t1, t2)
        loop = _make_loop(reg)
        state = _make_state()

        events: list = []
        append_order: list[str] = []
        original_append = loop._session_manager.append_message

        async def tracked_append(*args, **kwargs):
            await original_append(*args, **kwargs)
            if len(args) > 1 and args[1] == "tool":
                append_order.append(f"append_tool:{kwargs.get('tool_call_id', '?')}")

        loop._session_manager.append_message = AsyncMock(side_effect=tracked_append)

        tool_calls = [_tc("a", call_id="c1"), _tc("b", call_id="c2")]
        async for ev in _handle_tool_calls(
            loop, state, 0, "text", tool_calls, _passed_guard(),
        ):
            if isinstance(ev, ToolCallInfo):
                # When ToolCallInfo is yielded, no tool results should be appended yet
                events.append(("info", ev.tool_name, len(append_order)))

        # Both ToolCallInfo events were yielded before any tool append
        assert len(events) == 2
        assert events[0] == ("info", "a", 0)  # 0 appends happened before first info
        assert events[1] == ("info", "b", 0)  # 0 appends happened before second info

    @pytest.mark.asyncio
    async def test_denied_event_after_execution(self):
        """ToolDenied events appear after the group's ToolCallInfo batch."""
        tool = _CodingOnlyStubTool("restricted")
        reg = _make_registry(tool)
        loop = _make_loop(reg)
        state = _make_state()
        state.mode = ToolMode.chat_safe

        events: list = []
        async for ev in _handle_tool_calls(
            loop, state, 0, "text", [_tc("restricted")], _passed_guard(),
        ):
            events.append(type(ev).__name__)

        assert events == ["ToolCallInfo", "ToolDenied"]

    @pytest.mark.asyncio
    async def test_failure_signals_merged_in_order(self):
        """Failure signals from multiple tools are accumulated in call order."""
        t1 = _StubTool("a", read_only=True, concurrency_safe=True)
        t2 = _StubTool("b", read_only=True, concurrency_safe=True)
        reg = _make_registry(t1, t2)
        loop = _make_loop(reg)
        loop._execute_tool = AsyncMock(
            side_effect=[
                {"ok": False, "error_code": "EXECUTION_ERROR", "message": "fail_a"},
                {"ok": False, "error_code": "EXECUTION_ERROR", "message": "fail_b"},
            ]
        )
        state = _make_state()

        async for _ in _handle_tool_calls(
            loop, state, 0, "text",
            [_tc("a"), _tc("b")], _passed_guard(),
        ):
            pass

        assert state.accumulated_failure_signals == [
            "tool_failure:a",
            "tool_failure:b",
        ]

    @pytest.mark.asyncio
    async def test_transcript_append_order(self):
        """Tool results are appended to transcript in original call order."""
        t1 = _StubTool("a", read_only=True, concurrency_safe=True)
        t2 = _StubTool("b", read_only=True, concurrency_safe=True)
        reg = _make_registry(t1, t2)
        loop = _make_loop(reg)
        state = _make_state()

        async for _ in _handle_tool_calls(
            loop, state, 0, "text",
            [_tc("a", call_id="c1"), _tc("b", call_id="c2")], _passed_guard(),
        ):
            pass

        # First call is assistant message, then tool results in order
        append_calls = loop._session_manager.append_message.call_args_list
        tool_calls_appended = [
            c.kwargs["tool_call_id"] for c in append_calls if "tool_call_id" in c.kwargs
        ]
        assert tool_calls_appended == ["c1", "c2"]


class TestExecuteGroup:
    """_execute_group dispatches to serial or parallel paths."""

    @pytest.mark.asyncio
    async def test_serial_barrier(self):
        tool = _StubTool("w")
        reg = _make_registry(tool)
        loop = _make_loop(reg)
        state = _make_state()
        group = _ExecutionGroup(tool_calls=[_tc("w")], parallel=False, start_index=0)
        outcomes = await _execute_group(
            loop, state, group, _passed_guard(),
            session_id="test-session", iteration=0,
        )
        assert len(outcomes) == 1
        assert outcomes[0].result["ok"] is True

    @pytest.mark.asyncio
    async def test_single_parallel_runs_serially(self):
        """A parallel group with 1 element runs serially (no TaskGroup overhead)."""
        tool = _StubTool("a", read_only=True, concurrency_safe=True)
        reg = _make_registry(tool)
        loop = _make_loop(reg)
        state = _make_state()
        group = _ExecutionGroup(tool_calls=[_tc("a")], parallel=True, start_index=0)
        outcomes = await _execute_group(
            loop, state, group, _passed_guard(),
            session_id="test-session", iteration=0,
        )
        assert len(outcomes) == 1

    @pytest.mark.asyncio
    async def test_multi_parallel_uses_taskgroup(self):
        t1 = _StubTool("a", read_only=True, concurrency_safe=True, delay=0.05)
        t2 = _StubTool("b", read_only=True, concurrency_safe=True, delay=0.05)
        reg = _make_registry(t1, t2)
        loop = _make_loop(reg)
        state = _make_state()
        group = _ExecutionGroup(
            tool_calls=[_tc("a"), _tc("b")], parallel=True, start_index=0,
        )

        start = time.monotonic()
        outcomes = await _execute_group(
            loop, state, group, _passed_guard(),
            session_id="test-session", iteration=0,
        )
        elapsed = time.monotonic() - start

        assert len(outcomes) == 2
        assert elapsed < 0.09


# ===========================================================================
# Regression: existing semantics unchanged
# ===========================================================================


class TestRegressionModeDenied:
    """Mode-denied tools still produce ToolDenied and failure signals."""

    @pytest.mark.asyncio
    async def test_mode_denied_in_group(self):
        tool = _CodingOnlyStubTool("restricted")
        reg = _make_registry(tool)
        loop = _make_loop(reg)
        state = _make_state()
        state.mode = ToolMode.chat_safe

        outcome = await _run_single_tool(loop, state, _tc("restricted"), _passed_guard())
        assert outcome.denied_event is not None
        assert outcome.denied_event.error_code == "MODE_DENIED"
        assert outcome.failure_signal == "guard_denied:restricted"


class TestRegressionGuardDenied:
    """Guardrail-blocked tools still produce correct failure signals."""

    @pytest.mark.asyncio
    async def test_guard_denied_signal(self):
        tool = _StubTool("high_risk")
        reg = _make_registry(tool)
        loop = _make_loop(reg)
        loop._execute_tool = AsyncMock(return_value={
            "ok": False,
            "error_code": "GUARD_ANCHOR_MISSING",
            "message": "blocked",
        })
        state = _make_state()

        outcome = await _run_single_tool(loop, state, _tc("high_risk"), _passed_guard())
        assert outcome.failure_signal == "guard_denied:high_risk"

    @pytest.mark.asyncio
    async def test_tool_failure_signal(self):
        tool = _StubTool("broken")
        reg = _make_registry(tool)
        loop = _make_loop(reg)
        loop._execute_tool = AsyncMock(return_value={
            "ok": False,
            "error_code": "EXECUTION_ERROR",
            "message": "boom",
        })
        state = _make_state()

        outcome = await _run_single_tool(loop, state, _tc("broken"), _passed_guard())
        assert outcome.failure_signal == "tool_failure:broken"


class TestMaxParallelToolsConstant:
    """V1 bounded parallelism constant is 3."""

    def test_value(self):
        assert _MAX_PARALLEL_TOOLS == 3


# ===========================================================================
# OI-M2-04 Hotfix: Write tool request-level circuit breaker
# ===========================================================================


class TestWriteToolRequestLimit:
    """Per-request limit on the same non-read-only tool (OI-M2-04)."""

    @pytest.mark.asyncio
    async def test_write_tool_request_limit_triggered(self):
        """Same write tool executed 3 times OK; 4th is rejected."""
        write_tool = _StubTool("memory_append", read_only=False)
        reg = _make_registry(write_tool)
        loop = _make_loop(reg)
        state = _make_state()

        for i in range(3):
            outcome = await _run_single_tool(loop, state, _tc("memory_append"), _passed_guard())
            assert outcome.result.get("ok", True) is True, f"call {i+1} should succeed"

        outcome = await _run_single_tool(loop, state, _tc("memory_append"), _passed_guard())
        assert outcome.result["ok"] is False
        assert outcome.result["error_code"] == "WRITE_TOOL_REQUEST_LIMIT"
        assert outcome.failure_signal == "tool_failure:memory_append"

    @pytest.mark.asyncio
    async def test_write_tool_interleaved_with_readonly(self):
        """Read-only tools don't count; write tool accumulates across interleaving."""
        write_tool = _StubTool("memory_append", read_only=False)
        read_tool = _StubTool("current_time", read_only=True)
        reg = _make_registry(write_tool, read_tool)
        loop = _make_loop(reg)
        state = _make_state()

        # write, read, write, read, write (3 writes OK), write (4th rejected)
        await _run_single_tool(loop, state, _tc("memory_append"), _passed_guard())
        await _run_single_tool(loop, state, _tc("current_time"), _passed_guard())
        await _run_single_tool(loop, state, _tc("memory_append"), _passed_guard())
        await _run_single_tool(loop, state, _tc("current_time"), _passed_guard())
        outcome3 = await _run_single_tool(loop, state, _tc("memory_append"), _passed_guard())
        assert outcome3.result.get("ok", True) is True  # 3rd write OK

        outcome4 = await _run_single_tool(loop, state, _tc("memory_append"), _passed_guard())
        assert outcome4.result["ok"] is False
        assert outcome4.result["error_code"] == "WRITE_TOOL_REQUEST_LIMIT"

    @pytest.mark.asyncio
    async def test_write_tool_different_tools_independent(self):
        """Different write tools have independent counters."""
        tool_a = _StubTool("tool_a", read_only=False)
        tool_b = _StubTool("tool_b", read_only=False)
        reg = _make_registry(tool_a, tool_b)
        loop = _make_loop(reg)
        state = _make_state()

        for _ in range(3):
            await _run_single_tool(loop, state, _tc("tool_a"), _passed_guard())
            await _run_single_tool(loop, state, _tc("tool_b"), _passed_guard())

        # 4th call to each: both rejected independently
        outcome_a = await _run_single_tool(loop, state, _tc("tool_a"), _passed_guard())
        assert outcome_a.result["error_code"] == "WRITE_TOOL_REQUEST_LIMIT"
        outcome_b = await _run_single_tool(loop, state, _tc("tool_b"), _passed_guard())
        assert outcome_b.result["error_code"] == "WRITE_TOOL_REQUEST_LIMIT"

    @pytest.mark.asyncio
    async def test_read_only_tool_not_limited(self):
        """Read-only tools are never limited."""
        read_tool = _StubTool("current_time", read_only=True)
        reg = _make_registry(read_tool)
        loop = _make_loop(reg)
        state = _make_state()

        for _ in range(10):
            outcome = await _run_single_tool(loop, state, _tc("current_time"), _passed_guard())
            assert outcome.result.get("ok", True) is True

    @pytest.mark.asyncio
    async def test_procedure_action_not_limited(self):
        """Procedure actions bypass the circuit breaker entirely."""
        write_tool = _StubTool("memory_append", read_only=False)
        reg = _make_registry(write_tool)
        loop = _make_loop(reg)
        state = _make_state()
        # Simulate a procedure action by putting it in the action map
        state.procedure_action_map = {"do_something": "action_spec"}
        # Procedure action branch requires _procedure_runtime on loop
        loop._procedure_runtime = None  # will hit early return, but NOT the breaker

        outcome = await _run_single_tool(loop, state, _tc("do_something"), _passed_guard())
        # The procedure action path returns PROCEDURE_UNKNOWN (no runtime),
        # but it never hits the write tool breaker
        assert outcome.result.get("error_code") != "WRITE_TOOL_REQUEST_LIMIT"
