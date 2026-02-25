"""Integration tests for M1.5 Tool Modes — WebSocket end-to-end.

Covers:
- tool_denied frame sent over WebSocket for denied tools
- Dialog continues after denial (done=true still arrives)
- Allowed tools execute normally in chat_safe
- Denial persisted to session transcript in DB
- tool_denied type is distinguishable from other event types
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from src.agent.agent import AgentLoop
from src.agent.model_client import ContentDelta, ModelClient, StreamEvent, ToolCallsComplete
from src.agent.provider_registry import AgentLoopRegistry
from src.constants import DB_SCHEMA
from src.session.manager import SessionManager
from src.session.models import Base
from src.tools.builtins import register_builtins
from src.tools.registry import ToolRegistry

pytestmark = pytest.mark.integration

# _handle_chat_send calls get_settings() which needs OPENAI_API_KEY
os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")


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


def _make_app(pg_url: str, tmp_path: Path):
    """Create a FastAPI app with real builtins registry for tool mode testing."""
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
        register_builtins(registry, tmp_path)

        agent_loop = AgentLoop(
            model_client=fake_model,
            session_manager=session_manager,
            workspace_dir=tmp_path,
            model="test-model",
            tool_registry=registry,
        )

        # Build registry (M6: _handle_chat_send uses registry)
        loop_registry = AgentLoopRegistry(default_provider="openai")
        loop_registry.register("openai", agent_loop, "test-model")

        app.state.agent_loop_registry = loop_registry
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
    """Send chat.send RPC and collect all responses until done or error.

    Handles tool_denied as a pass-through (not a termination event).
    """
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


class TestToolDeniedWebSocket:
    """WebSocket receives tool_denied frame when model calls restricted tool."""

    def test_tool_denied_frame_sent(self, pg_url, tmp_path):
        """Model calls read_file in chat_safe → client receives tool_denied frame."""
        app, model = _make_app(pg_url, tmp_path)
        model.set_responses(
            # Model hallucinates read_file in chat_safe
            [ToolCallsComplete(tool_calls=[{
                "id": "call_rf", "name": "read_file", "arguments": '{"path": "test.md"}'
            }])],
            # Model continues with text after denial
            [ContentDelta(text="I cannot read files in this mode.")],
        )

        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            messages = _send_and_collect(ws, content="read the file")

            denied = [m for m in messages if m["type"] == "tool_denied"]
            assert len(denied) == 1
            d = denied[0]["data"]
            assert d["tool_name"] == "read_file"
            assert d["call_id"] == "call_rf"
            assert d["mode"] == "chat_safe"
            assert d["error_code"] == "MODE_DENIED"
            assert d["message"] != ""
            assert d["next_action"] != ""

    def test_tool_denied_has_all_six_fields(self, pg_url, tmp_path):
        """ToolDeniedData has exactly the 6 specified fields."""
        app, model = _make_app(pg_url, tmp_path)
        model.set_responses(
            [ToolCallsComplete(tool_calls=[{
                "id": "c1", "name": "read_file", "arguments": '{"path": "a"}'
            }])],
            [ContentDelta(text="denied")],
        )

        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            messages = _send_and_collect(ws, content="test")
            denied = [m for m in messages if m["type"] == "tool_denied"][0]
            expected_fields = {
                "call_id", "tool_name", "mode", "error_code", "message", "next_action",
            }
            assert set(denied["data"].keys()) == expected_fields


class TestToolDeniedContinuation:
    """Conversation continues after tool_denied (not terminal)."""

    def test_text_continues_after_denial(self, pg_url, tmp_path):
        """After tool_denied, model's next response arrives and done=true is sent."""
        app, model = _make_app(pg_url, tmp_path)
        model.set_responses(
            [ToolCallsComplete(tool_calls=[{
                "id": "cf", "name": "read_file", "arguments": '{"path": "x"}'
            }])],
            [ContentDelta(text="Sorry, I cannot do that in this mode.")],
        )

        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            messages = _send_and_collect(ws, content="read file")

            # Should have tool_denied + stream content + done
            denied = [m for m in messages if m["type"] == "tool_denied"]
            assert len(denied) == 1

            content_chunks = [
                m for m in messages
                if m["type"] == "stream_chunk" and not m["data"]["done"]
            ]
            assert len(content_chunks) > 0
            text_content = "".join(c["data"]["content"] for c in content_chunks)
            assert "cannot" in text_content.lower()

            done_msgs = [
                m for m in messages
                if m["type"] == "stream_chunk" and m["data"]["done"]
            ]
            assert len(done_msgs) == 1


class TestAllowedToolWorks:
    """chat_safe allowed tools execute normally (regression)."""

    def test_current_time_executes_in_chat_safe(self, pg_url, tmp_path):
        """current_time should execute without denial."""
        app, model = _make_app(pg_url, tmp_path)
        model.set_responses(
            [ToolCallsComplete(tool_calls=[{
                "id": "ct1", "name": "current_time", "arguments": "{}"
            }])],
            [ContentDelta(text="The current time is 12:00")],
        )

        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            messages = _send_and_collect(ws, content="what time is it")

            denied = [m for m in messages if m["type"] == "tool_denied"]
            assert len(denied) == 0

            tool_calls = [m for m in messages if m["type"] == "tool_call"]
            assert len(tool_calls) == 1
            assert tool_calls[0]["data"]["tool_name"] == "current_time"

            content = "".join(
                m["data"]["content"] for m in messages
                if m["type"] == "stream_chunk" and not m["data"]["done"]
            )
            assert "12:00" in content


class TestDenialPersistedToTranscript:
    """Denied tool call result persisted as tool role message in DB."""

    def test_denial_in_transcript(self, pg_url, tmp_path):
        app, model = _make_app(pg_url, tmp_path)
        model.set_responses(
            [ToolCallsComplete(tool_calls=[{
                "id": "c_persist", "name": "read_file", "arguments": '{"path": "z"}'
            }])],
            [ContentDelta(text="denied response")],
        )

        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            messages = _send_and_collect(ws, content="read z", session_id="persist-test")

            # Verify denial happened
            denied = [m for m in messages if m["type"] == "tool_denied"]
            assert len(denied) == 1

        # Now check DB for the tool role message with MODE_DENIED
        import asyncio

        async def check_db():
            engine = create_async_engine(pg_url, echo=False)
            async with engine.begin() as conn:
                result = await conn.execute(
                    text(
                        f"SELECT content FROM {DB_SCHEMA}.messages "
                        f"WHERE session_id = 'persist-test' AND role = 'tool' "
                        f"ORDER BY seq"
                    )
                )
                rows = result.fetchall()
            await engine.dispose()
            return rows

        rows = asyncio.get_event_loop().run_until_complete(check_db())
        assert len(rows) >= 1

        # At least one tool message should contain MODE_DENIED
        found = False
        for row in rows:
            content = json.loads(row[0])
            if content.get("error_code") == "MODE_DENIED":
                found = True
                assert content["tool_name"] == "read_file"
                assert content["mode"] == "chat_safe"
                break
        assert found, "MODE_DENIED tool message not found in transcript"


class TestUnknownToolNotDenied:
    """Unknown tool must NOT produce tool_denied frame (protocol boundary)."""

    def test_unknown_tool_no_denied_frame(self, pg_url, tmp_path):
        """Model calls nonexistent tool → no tool_denied, UNKNOWN_TOOL in tool result."""
        app, model = _make_app(pg_url, tmp_path)
        model.set_responses(
            [ToolCallsComplete(tool_calls=[{
                "id": "call_ghost", "name": "nonexistent_tool", "arguments": "{}"
            }])],
            [ContentDelta(text="That tool does not exist.")],
        )

        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            messages = _send_and_collect(ws, content="use ghost tool")

            # No tool_denied frame — unknown tool is not a mode denial
            denied = [m for m in messages if m["type"] == "tool_denied"]
            assert len(denied) == 0

            # tool_call frame IS present (ToolCallInfo still yielded)
            tool_calls = [m for m in messages if m["type"] == "tool_call"]
            assert len(tool_calls) == 1
            assert tool_calls[0]["data"]["tool_name"] == "nonexistent_tool"

            # Conversation continues — done=true arrives
            done = [
                m for m in messages
                if m["type"] == "stream_chunk" and m["data"]["done"]
            ]
            assert len(done) == 1

    def test_unknown_tool_result_persisted_as_unknown(self, pg_url, tmp_path):
        """UNKNOWN_TOOL error_code persisted in session transcript."""
        app, model = _make_app(pg_url, tmp_path)
        model.set_responses(
            [ToolCallsComplete(tool_calls=[{
                "id": "call_ghost2", "name": "ghost_tool", "arguments": "{}"
            }])],
            [ContentDelta(text="Sorry")],
        )

        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            _send_and_collect(ws, content="test", session_id="unknown-test")

        import asyncio

        async def check_db():
            engine = create_async_engine(pg_url, echo=False)
            async with engine.begin() as conn:
                result = await conn.execute(
                    text(
                        f"SELECT content FROM {DB_SCHEMA}.messages "
                        f"WHERE session_id = 'unknown-test' AND role = 'tool' "
                        f"ORDER BY seq"
                    )
                )
                rows = result.fetchall()
            await engine.dispose()
            return rows

        rows = asyncio.get_event_loop().run_until_complete(check_db())
        assert len(rows) >= 1
        # Search all tool messages — don't assume row order
        found = False
        for row in rows:
            content = json.loads(row[0])
            if content.get("error_code") == "UNKNOWN_TOOL":
                found = True
                assert "ghost_tool" in content["message"]
                break
        assert found, "UNKNOWN_TOOL tool message not found in transcript"


class TestToolDeniedTypeDistinguishable:
    """tool_denied type is distinct from stream_chunk, tool_call, error."""

    def test_type_field_is_tool_denied(self, pg_url, tmp_path):
        app, model = _make_app(pg_url, tmp_path)
        model.set_responses(
            [ToolCallsComplete(tool_calls=[{
                "id": "td1", "name": "read_file", "arguments": '{"path": "x"}'
            }])],
            [ContentDelta(text="after")],
        )

        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            messages = _send_and_collect(ws, content="test")

            types = {m["type"] for m in messages}
            assert "tool_denied" in types
            # Verify it coexists with other types
            assert "stream_chunk" in types
            assert "tool_call" in types

            # tool_denied must not be confused with error
            errors = [m for m in messages if m["type"] == "error"]
            assert len(errors) == 0
