"""End-to-end tool loop flow tests via WebSocket.

Verifies the full request→response cycle including:
- Single tool call round trip
- Multi-round tool calls
- Content + tool_calls mixed stream
- Tool execution failure
- Max iterations safety
- Fencing interception mid-loop
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from src.agent.agent import MAX_TOOL_ITERATIONS, AgentLoop
from src.agent.model_client import ContentDelta, ModelClient, StreamEvent, ToolCallsComplete
from src.constants import DB_SCHEMA
from src.session.manager import SessionManager
from src.session.models import Base
from src.tools.base import BaseTool, ToolGroup, ToolMode
from src.tools.registry import ToolRegistry

pytestmark = pytest.mark.integration


class FakeModelClient(ModelClient):
    """Mock model client with configurable response sequences."""

    def __init__(self) -> None:
        self._responses: list[list[StreamEvent]] = []
        self._call_idx = 0

    def set_responses(self, *sequences: list[StreamEvent]) -> None:
        self._responses = list(sequences)
        self._call_idx = 0

    async def chat(
        self, messages: list[dict[str, Any]], model: str, temperature: float | None = None
    ) -> str:
        return ""

    async def chat_stream(
        self, messages: list[dict[str, Any]], model: str, *, tools: list[dict] | None = None
    ) -> AsyncIterator[str]:
        yield ""

    async def chat_completion(self, messages, model, *, tools=None):
        raise NotImplementedError

    async def chat_stream_with_tools(
        self, messages: list[dict[str, Any]], model: str, *, tools: list[dict] | None = None
    ) -> AsyncIterator[StreamEvent]:
        idx = self._call_idx
        self._call_idx += 1
        if idx < len(self._responses):
            for event in self._responses[idx]:
                yield event


class EchoTool(BaseTool):
    """Test tool that echoes its arguments."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def description(self) -> str:
        return "Echo arguments back"

    @property
    def group(self) -> ToolGroup:
        return ToolGroup.world

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.chat_safe, ToolMode.coding})

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {"text": {"type": "string"}}}

    async def execute(self, arguments: dict) -> dict:
        return {"echoed": arguments.get("text", "")}


class FailingTool(BaseTool):
    """Test tool that always raises."""

    @property
    def name(self) -> str:
        return "failing_tool"

    @property
    def description(self) -> str:
        return "Always fails"

    @property
    def group(self) -> ToolGroup:
        return ToolGroup.world

    @property
    def allowed_modes(self) -> frozenset[ToolMode]:
        return frozenset({ToolMode.chat_safe, ToolMode.coding})

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self, arguments: dict) -> dict:
        raise RuntimeError("Tool execution failed!")


def _make_app(pg_url: str, tmp_path, *, tools: list[BaseTool] | None = None):
    """Create a FastAPI app with its own engine (created within TestClient's event loop)."""
    from src.gateway.app import _handle_rpc_message

    fake_model = FakeModelClient()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_async_engine(pg_url, echo=False)
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}"))
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(f"TRUNCATE {DB_SCHEMA}.messages, {DB_SCHEMA}.sessions CASCADE")
            )
        db_session_factory = async_sessionmaker(engine, expire_on_commit=False)

        session_manager = SessionManager(db_session_factory=db_session_factory)
        registry = ToolRegistry()
        for tool in (tools or []):
            registry.register(tool)

        agent_loop = AgentLoop(
            model_client=fake_model,
            session_manager=session_manager,
            workspace_dir=tmp_path,
            model="test-model",
            tool_registry=registry,
        )

        app.state.agent_loop = agent_loop
        app.state.session_manager = session_manager
        app.state.fake_model = fake_model
        app.state.db_session_factory = db_session_factory

        yield

        await engine.dispose()

    app = FastAPI(lifespan=lifespan)

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                raw = await websocket.receive_text()
                await _handle_rpc_message(websocket, raw)
        except WebSocketDisconnect:
            pass

    return app, fake_model


def _send_and_collect(ws, *, content: str, session_id: str = "main", request_id: str = "req-1"):
    """Send a chat.send RPC and collect all response messages until done or error."""
    msg = {
        "type": "request",
        "id": request_id,
        "method": "chat.send",
        "params": {"content": content, "session_id": session_id},
    }
    ws.send_text(json.dumps(msg))

    messages = []
    while True:
        data = json.loads(ws.receive_text())
        messages.append(data)
        if data.get("type") == "stream_chunk" and data["data"]["done"]:
            break
        if data.get("type") == "error":
            break
    return messages


class TestSingleToolCall:
    """LLM returns tool_call → execute → final answer."""

    def test_single_tool_round_trip(self, pg_url, tmp_path):
        app, model = _make_app(pg_url, tmp_path, tools=[EchoTool()])
        model.set_responses(
            [ToolCallsComplete(tool_calls=[{
                "id": "call_1", "name": "echo", "arguments": '{"text": "ping"}'
            }])],
            [ContentDelta(text="Got: ping")],
        )

        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            messages = _send_and_collect(ws, content="echo test")

            tool_calls = [m for m in messages if m["type"] == "tool_call"]
            assert len(tool_calls) == 1
            assert tool_calls[0]["data"]["tool_name"] == "echo"

            content = "".join(
                m["data"]["content"] for m in messages
                if m["type"] == "stream_chunk" and not m["data"]["done"]
            )
            assert "Got: ping" in content


class TestMultiRoundToolCalls:
    """LLM calls tools twice before final answer."""

    def test_two_round_tool_loop(self, pg_url, tmp_path):
        app, model = _make_app(pg_url, tmp_path, tools=[EchoTool()])
        model.set_responses(
            [ToolCallsComplete(tool_calls=[{
                "id": "call_1", "name": "echo", "arguments": '{"text": "first"}'
            }])],
            [ToolCallsComplete(tool_calls=[{
                "id": "call_2", "name": "echo", "arguments": '{"text": "second"}'
            }])],
            [ContentDelta(text="Done after 2 tools")],
        )

        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            messages = _send_and_collect(ws, content="multi tool")

            tool_calls = [m for m in messages if m["type"] == "tool_call"]
            assert len(tool_calls) == 2


class TestMixedContentAndToolCalls:
    """LLM streams content tokens then tool_calls in the same response."""

    def test_content_before_tool_call(self, pg_url, tmp_path):
        app, model = _make_app(pg_url, tmp_path, tools=[EchoTool()])
        model.set_responses(
            [
                ContentDelta(text="Let me check... "),
                ToolCallsComplete(tool_calls=[{
                    "id": "call_1", "name": "echo", "arguments": '{"text": "check"}'
                }]),
            ],
            [ContentDelta(text="Result found")],
        )

        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            messages = _send_and_collect(ws, content="mixed test")

            content_chunks = [
                m for m in messages
                if m["type"] == "stream_chunk" and not m["data"]["done"]
            ]
            tool_calls = [m for m in messages if m["type"] == "tool_call"]

            all_content = "".join(c["data"]["content"] for c in content_chunks)
            assert "Let me check" in all_content
            assert "Result found" in all_content
            assert len(tool_calls) == 1


class TestToolExecutionFailure:
    """Tool.execute raises → error result in tool message, loop continues."""

    def test_failing_tool_returns_error(self, pg_url, tmp_path):
        app, model = _make_app(pg_url, tmp_path, tools=[FailingTool()])
        model.set_responses(
            [ToolCallsComplete(tool_calls=[{
                "id": "call_fail", "name": "failing_tool", "arguments": "{}"
            }])],
            [ContentDelta(text="Tool failed, sorry")],
        )

        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            messages = _send_and_collect(ws, content="try failing tool")

            content = "".join(
                m["data"]["content"] for m in messages
                if m["type"] == "stream_chunk" and not m["data"]["done"]
            )
            assert "Tool failed" in content


class TestMaxIterationsSafety:
    """Exceeding MAX_TOOL_ITERATIONS yields safety message."""

    def test_max_iterations_limit(self, pg_url, tmp_path):
        app, model = _make_app(pg_url, tmp_path, tools=[EchoTool()])

        responses = []
        for i in range(MAX_TOOL_ITERATIONS + 1):
            responses.append(
                [ToolCallsComplete(tool_calls=[{
                    "id": f"call_{i}", "name": "echo", "arguments": '{"text": "loop"}'
                }])]
            )
        model.set_responses(*responses)

        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            messages = _send_and_collect(ws, content="infinite loop")

            content = "".join(
                m["data"]["content"] for m in messages
                if m["type"] == "stream_chunk" and not m["data"]["done"]
            )
            assert "maximum number of tool calls" in content.lower()


class TestFencingMidLoop:
    """SessionFencingError during tool loop → RPC error to client."""

    def test_fencing_error_returns_session_fenced(self, pg_url, tmp_path):
        app, model = _make_app(pg_url, tmp_path, tools=[EchoTool()])
        model.set_responses(
            [ToolCallsComplete(tool_calls=[{
                "id": "call_1", "name": "echo", "arguments": '{"text": "test"}'
            }])],
            [ContentDelta(text="Should not reach")],
        )

        # Store original execute and create a version that steals the lock
        original_execute = EchoTool.execute

        async def stealing_execute(self_tool, arguments):
            # Steal the lock mid-execution by updating lock_token directly
            db_factory = app.state.db_session_factory
            async with db_factory() as db:
                await db.execute(
                    text(
                        f"UPDATE {DB_SCHEMA}.sessions SET lock_token = 'stolen-token' "
                        "WHERE id = 'main'"
                    )
                )
                await db.commit()
            return await original_execute(self_tool, arguments)

        EchoTool.execute = stealing_execute

        try:
            with TestClient(app) as client, client.websocket_connect("/ws") as ws:
                msg = {
                    "type": "request",
                    "id": "req-fenced",
                    "method": "chat.send",
                    "params": {"content": "fencing test", "session_id": "main"},
                }
                ws.send_text(json.dumps(msg))

                messages = []
                while True:
                    data = json.loads(ws.receive_text())
                    messages.append(data)
                    if data.get("type") == "stream_chunk" and data["data"]["done"]:
                        break
                    if data.get("type") == "error":
                        break

                errors = [m for m in messages if m["type"] == "error"]
                assert len(errors) == 1
                assert errors[0]["error"]["code"] == "SESSION_FENCED"
        finally:
            EchoTool.execute = original_execute
