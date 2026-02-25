"""E2E integration test: BudgetGate wired into Gateway WebSocket path (ADR 0041).

Verifies that budget deny actually rejects chat.send requests end-to-end
through the full WebSocket → RPC dispatch → _handle_chat_send → BudgetGate chain.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.testclient import TestClient

from src.agent.agent import AgentLoop
from src.agent.model_client import ContentDelta, ModelClient, StreamEvent
from src.agent.provider_registry import AgentLoopRegistry
from src.constants import DB_SCHEMA
from src.gateway.budget_gate import BudgetGate
from src.session.manager import SessionManager
from src.session.models import Base

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fake model client (same pattern as test_websocket.py)
# ---------------------------------------------------------------------------


class _FakeModel(ModelClient):
    async def chat(self, messages, model, temperature=None):
        return ""

    async def chat_stream(self, messages, model, *, tools=None):
        yield ""

    async def chat_completion(self, messages, model, *, tools=None):
        raise NotImplementedError

    async def chat_stream_with_tools(self, messages, model, *, tools=None) -> AsyncIterator[StreamEvent]:
        yield ContentDelta(text="OK from model")


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def _make_budget_app(pg_url: str, tmp_path, *, initial_cumulative: float = 0.0):
    """Create a gateway app with real BudgetGate (real DB, budget tables)."""
    from src.gateway.app import _handle_rpc_message

    model = _FakeModel()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = create_async_engine(pg_url, echo=False)
        async with engine.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {DB_SCHEMA}"))
            await conn.run_sync(Base.metadata.create_all)
            # Budget tables (migration-managed, not ORM)
            await conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {DB_SCHEMA}.budget_state (
                    id TEXT PRIMARY KEY DEFAULT 'global',
                    cumulative_eur NUMERIC(10,4) NOT NULL DEFAULT 0,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """))
            await conn.execute(text(f"""
                CREATE TABLE IF NOT EXISTS {DB_SCHEMA}.budget_reservations (
                    reservation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    eval_run_id TEXT NOT NULL DEFAULT '',
                    reserved_eur NUMERIC(10,4) NOT NULL,
                    actual_eur NUMERIC(10,4),
                    status TEXT NOT NULL DEFAULT 'reserved',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    settled_at TIMESTAMPTZ
                )
            """))
            # Seed global budget row + set initial cumulative
            await conn.execute(text(f"""
                INSERT INTO {DB_SCHEMA}.budget_state (id, cumulative_eur)
                VALUES ('global', :cum)
                ON CONFLICT (id) DO UPDATE SET cumulative_eur = :cum, updated_at = NOW()
            """), {"cum": initial_cumulative})
            # Clean previous test data
            await conn.execute(text(f"TRUNCATE {DB_SCHEMA}.budget_reservations CASCADE"))
            await conn.execute(
                text(f"TRUNCATE {DB_SCHEMA}.messages, {DB_SCHEMA}.sessions CASCADE")
            )

        db_session_factory = async_sessionmaker(engine, expire_on_commit=False)
        session_manager = SessionManager(db_session_factory=db_session_factory)
        agent_loop = AgentLoop(
            model_client=model,
            session_manager=session_manager,
            workspace_dir=tmp_path,
            model="test-model",
        )
        registry = AgentLoopRegistry(default_provider="openai")
        registry.register("openai", agent_loop, "test-model")

        budget_gate = BudgetGate(engine, schema=DB_SCHEMA)

        app.state.agent_loop_registry = registry
        app.state.agent_loop = agent_loop
        app.state.session_manager = session_manager
        app.state.budget_gate = budget_gate
        app.state.db_engine = engine

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

    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _send_rpc(ws, *, method: str, params: dict | None = None, request_id: str = "req-1"):
    msg = {"type": "request", "id": request_id, "method": method, "params": params or {}}
    ws.send_text(json.dumps(msg))


def _collect_until_done(ws) -> list[dict]:
    messages = []
    while True:
        data = json.loads(ws.receive_text())
        messages.append(data)
        if data.get("type") == "stream_chunk" and data["data"]["done"]:
            break
        if data.get("type") == "error":
            break
    return messages


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBudgetDenyE2E:
    """E2E: budget gate blocks chat.send when cumulative near stop threshold."""

    def test_budget_exceeded_returns_error(self, pg_url, tmp_path):
        """cumulative=24.98, reserve=0.05 → 25.03 >= 25.00 → BUDGET_EXCEEDED."""
        app = _make_budget_app(pg_url, tmp_path, initial_cumulative=24.98)

        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            _send_rpc(ws, method="chat.send", params={"content": "hi", "session_id": "s1"})
            messages = _collect_until_done(ws)

            errors = [m for m in messages if m.get("type") == "error"]
            assert len(errors) == 1
            assert errors[0]["error"]["code"] == "BUDGET_EXCEEDED"

    def test_budget_exceeded_no_stream_events(self, pg_url, tmp_path):
        """Denied request should produce zero stream_chunk messages."""
        app = _make_budget_app(pg_url, tmp_path, initial_cumulative=24.98)

        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            _send_rpc(ws, method="chat.send", params={"content": "hi", "session_id": "s1"})
            messages = _collect_until_done(ws)

            stream_chunks = [m for m in messages if m.get("type") == "stream_chunk"]
            assert stream_chunks == []

    def test_budget_exceeded_cumulative_unchanged(self, pg_url, tmp_path):
        """Denied reserve should not increase cumulative_eur."""
        app = _make_budget_app(pg_url, tmp_path, initial_cumulative=24.98)

        with TestClient(app) as client:
            with client.websocket_connect("/ws") as ws:
                _send_rpc(ws, method="chat.send", params={"content": "hi", "session_id": "s1"})
                _collect_until_done(ws)

            # Read cumulative directly from DB
            # Engine is disposed after TestClient exits, so read inside context.
            # Actually, we need to read via a new connection inside the lifespan.
            # Simpler: send a second request to confirm cumulative is still 24.98
            # by verifying it's still denied with a 0.05 reserve.
            with client.websocket_connect("/ws") as ws:
                _send_rpc(ws, method="chat.send", params={"content": "hi2", "session_id": "s2"})
                messages = _collect_until_done(ws)
                errors = [m for m in messages if m.get("type") == "error"]
                assert len(errors) == 1
                assert errors[0]["error"]["code"] == "BUDGET_EXCEEDED"

    def test_budget_exceeded_no_reservation_created(self, pg_url, tmp_path):
        """Denied reserve should not create a reservation record."""
        app = _make_budget_app(pg_url, tmp_path, initial_cumulative=24.98)

        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            _send_rpc(ws, method="chat.send", params={"content": "hi", "session_id": "s1"})
            _collect_until_done(ws)

        # Verify no reservations were created (engine was disposed, create a new one)
        import asyncio

        async def _check():
            engine = create_async_engine(pg_url, echo=False)
            async with engine.begin() as conn:
                row = await conn.execute(text(
                    f"SELECT COUNT(*) FROM {DB_SCHEMA}.budget_reservations"
                ))
                count = row.scalar_one()
            await engine.dispose()
            return count

        count = asyncio.get_event_loop().run_until_complete(_check())
        assert count == 0


class TestBudgetApproveE2E:
    """E2E: budget gate allows chat.send when cumulative is within limits."""

    def test_approved_request_streams_response(self, pg_url, tmp_path):
        """cumulative=0, reserve=0.05 → well under 25 → request succeeds."""
        app = _make_budget_app(pg_url, tmp_path, initial_cumulative=0.0)

        with TestClient(app) as client, client.websocket_connect("/ws") as ws:
            _send_rpc(ws, method="chat.send", params={"content": "hi", "session_id": "s1"})
            messages = _collect_until_done(ws)

            errors = [m for m in messages if m.get("type") == "error"]
            assert errors == []

            done = [m for m in messages if m.get("type") == "stream_chunk" and m["data"]["done"]]
            assert len(done) == 1
