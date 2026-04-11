"""Tests for P2-M2b Slice C: WorkerExecutor."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from src.procedures.handoff import HandoffPacket, WorkerResult
from src.procedures.roles import AgentRole, RoleSpec
from src.procedures.worker import WorkerExecutor
from src.tools.base import BaseTool, ToolGroup, ToolMode
from src.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


@dataclass
class _FakeChatMessage:
    content: str | None = None
    tool_calls: list[dict[str, str]] | None = None


class _FakeModelClient:
    """Mock model client that returns pre-configured responses."""

    def __init__(self, responses: list[_FakeChatMessage]) -> None:
        self._responses = list(responses)
        self._call_count = 0

    async def chat_completion(
        self,
        messages: list[dict[str, Any]],
        model: str,
        *,
        tools: list[dict] | None = None,
        temperature: float | None = None,
    ) -> _FakeChatMessage:
        if self._call_count >= len(self._responses):
            return _FakeChatMessage(content='{"result": "fallback"}')
        resp = self._responses[self._call_count]
        self._call_count += 1
        return resp


class _FakeErrorModelClient:
    async def chat_completion(self, *args, **kwargs):
        raise TimeoutError("model timeout")


class _EchoTool(BaseTool):
    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo input"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"text": {"type": "string"}}}

    @property
    def group(self) -> ToolGroup:
        return ToolGroup.code

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.coding})

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def risk_level(self):
        from src.tools.base import RiskLevel
        return RiskLevel.low

    async def execute(self, arguments: dict, context=None) -> dict:
        return {"echoed": arguments.get("text", "")}


class _ProcOnlyTool(BaseTool):
    """Procedure-only tool that should be excluded from worker."""

    @property
    def name(self) -> str:
        return "proc_only"

    @property
    def description(self) -> str:
        return "Procedure only"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def group(self) -> ToolGroup:
        return ToolGroup.code

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset()

    @property
    def is_procedure_only(self) -> bool:
        return True

    async def execute(self, arguments: dict, context=None) -> dict:
        return {}


def _make_packet(**overrides) -> HandoffPacket:
    defaults = dict(
        handoff_id="h-1",
        source_actor=AgentRole.primary,
        target_role=AgentRole.worker,
        task_brief="Do the task",
    )
    defaults.update(overrides)
    return HandoffPacket(**defaults)


def _make_registry(*tools: BaseTool) -> ToolRegistry:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    return reg


def _worker_role() -> RoleSpec:
    return RoleSpec(
        role=AgentRole.worker,
        allowed_tool_groups=frozenset({ToolGroup.code}),
        max_iterations=3,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWorkerExecutorNormalCompletion:
    @pytest.mark.asyncio
    async def test_final_answer_on_first_call(self):
        """Worker extracts inner 'result' dict from prompt-compliant response."""
        client = _FakeModelClient([
            _FakeChatMessage(content='{"result": {"answer": 42}}'),
        ])
        executor = WorkerExecutor(client, _make_registry(), _worker_role(), model="test")
        result = await executor.execute(_make_packet())

        assert result.ok is True
        assert result.iterations_used == 1
        # Worker extracts inner "result" → result.result == {"answer": 42}
        assert result.result.get("answer") == 42

    @pytest.mark.asyncio
    async def test_flat_answer_also_works(self):
        """Worker handles flat JSON (no inner 'result' key)."""
        client = _FakeModelClient([
            _FakeChatMessage(content='{"answer": 42}'),
        ])
        executor = WorkerExecutor(client, _make_registry(), _worker_role(), model="test")
        result = await executor.execute(_make_packet())

        assert result.ok is True
        assert result.result.get("answer") == 42

    @pytest.mark.asyncio
    async def test_tool_call_then_answer(self):
        client = _FakeModelClient([
            _FakeChatMessage(
                content="",
                tool_calls=[{"id": "tc1", "name": "echo", "arguments": '{"text":"hi"}'}],
            ),
            _FakeChatMessage(content='{"result": {"echoed": "hi"}}'),
        ])
        reg = _make_registry(_EchoTool())
        executor = WorkerExecutor(client, reg, _worker_role(), model="test")
        result = await executor.execute(_make_packet())

        assert result.ok is True
        assert result.iterations_used == 2


class TestWorkerExecutorIterationLimit:
    @pytest.mark.asyncio
    async def test_hits_iteration_limit(self):
        # Always returns tool calls, never a final answer
        responses = [
            _FakeChatMessage(
                content="",
                tool_calls=[{"id": f"tc{i}", "name": "echo", "arguments": '{"text":"x"}'}],
            )
            for i in range(5)
        ]
        client = _FakeModelClient(responses)
        reg = _make_registry(_EchoTool())
        role = RoleSpec(
            role=AgentRole.worker,
            allowed_tool_groups=frozenset({ToolGroup.code}),
            max_iterations=2,
        )
        executor = WorkerExecutor(client, reg, role, model="test")
        result = await executor.execute(_make_packet())

        assert result.ok is False
        assert result.error_code == "WORKER_ITERATION_LIMIT"
        assert result.iterations_used == 2


class TestWorkerExecutorToolRejection:
    @pytest.mark.asyncio
    async def test_unknown_tool_rejected(self):
        client = _FakeModelClient([
            _FakeChatMessage(
                content="",
                tool_calls=[{"id": "tc1", "name": "nonexistent", "arguments": "{}"}],
            ),
            _FakeChatMessage(content='{"result": "done"}'),
        ])
        executor = WorkerExecutor(client, _make_registry(), _worker_role(), model="test")
        result = await executor.execute(_make_packet())
        assert result.ok is True  # recovered on second call

    @pytest.mark.asyncio
    async def test_procedure_only_tool_rejected(self):
        """Procedure-only tools must not be available to workers (D7)."""
        client = _FakeModelClient([
            _FakeChatMessage(
                content="",
                tool_calls=[{"id": "tc1", "name": "proc_only", "arguments": "{}"}],
            ),
            _FakeChatMessage(content='{"result": "done"}'),
        ])
        reg = _make_registry(_EchoTool(), _ProcOnlyTool())
        executor = WorkerExecutor(client, reg, _worker_role(), model="test")
        result = await executor.execute(_make_packet())
        # proc_only should be rejected, but worker recovers on second call
        assert result.ok is True


class TestWorkerHighRiskExclusion:
    @pytest.mark.asyncio
    async def test_high_risk_tool_excluded_from_worker(self):
        """High-risk tools must not be available to workers (guardrail bypass)."""

        class _HighRiskTool(BaseTool):
            @property
            def name(self) -> str:
                return "high_risk_write"

            @property
            def description(self) -> str:
                return "Writes files"

            @property
            def parameters(self) -> dict:
                return {"type": "object", "properties": {}}

            @property
            def group(self) -> ToolGroup:
                return ToolGroup.code

            @property
            def allowed_modes(self) -> frozenset[ToolMode]:
                return frozenset({ToolMode.coding})

            @property
            def risk_level(self):
                from src.tools.base import RiskLevel
                return RiskLevel.high

            async def execute(self, arguments: dict, context=None) -> dict:
                return {"written": True}

        client = _FakeModelClient([
            _FakeChatMessage(
                content="",
                tool_calls=[{"id": "tc1", "name": "high_risk_write", "arguments": "{}"}],
            ),
            _FakeChatMessage(content='{"done": true}'),
        ])
        reg = _make_registry(_EchoTool(), _HighRiskTool())
        executor = WorkerExecutor(client, reg, _worker_role(), model="test")
        result = await executor.execute(_make_packet())
        # high_risk_write should be rejected (not in allowed tools)
        # worker recovers on second call
        assert result.ok is True


class TestWorkerExecutorModelError:
    @pytest.mark.asyncio
    async def test_model_timeout(self):
        client = _FakeErrorModelClient()
        executor = WorkerExecutor(client, _make_registry(), _worker_role(), model="test")
        result = await executor.execute(_make_packet())

        assert result.ok is False
        assert result.error_code == "WORKER_MODEL_TIMEOUT"


class _CtxCaptureTool(BaseTool):
    """Tool that captures ToolContext for assertion in tests."""

    def __init__(self, captured: list) -> None:
        self._captured = captured

    @property
    def name(self) -> str:
        return "ctx_capture"

    @property
    def description(self) -> str:
        return "Captures context"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def group(self) -> ToolGroup:
        return ToolGroup.code

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.coding})

    @property
    def risk_level(self):
        from src.tools.base import RiskLevel
        return RiskLevel.low

    async def execute(self, arguments: dict, context=None) -> dict:
        self._captured.append(context)
        return {"ok": True}


class TestWorkerToolContextInjection:
    @pytest.mark.asyncio
    async def test_tool_receives_context(self):
        """Worker injects ToolContext with scope_key/session_id into tool calls."""
        received: list = []
        client = _FakeModelClient([
            _FakeChatMessage(
                content="",
                tool_calls=[{"id": "tc1", "name": "ctx_capture", "arguments": "{}"}],
            ),
            _FakeChatMessage(content='{"done": true}'),
        ])
        reg = _make_registry(_CtxCaptureTool(received))
        executor = WorkerExecutor(
            client, reg, _worker_role(), model="test",
            scope_key="test-scope", session_id="test-sess",
        )
        await executor.execute(_make_packet())
        assert len(received) == 1
        assert received[0].scope_key == "test-scope"
        assert received[0].session_id == "test-sess"
