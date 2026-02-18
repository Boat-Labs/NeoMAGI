from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from src.agent.agent import AgentLoop
from src.agent.events import TextChunk, ToolCallInfo
from src.agent.model_client import OpenAICompatModelClient
from src.config.settings import get_settings
from src.gateway.protocol import (
    ChatHistoryParams,
    ChatSendParams,
    RPCError,
    RPCErrorData,
    RPCHistoryResponse,
    RPCHistoryResponseData,
    RPCStreamChunk,
    RPCToolCall,
    StreamChunkData,
    ToolCallData,
    parse_rpc_request,
)
from src.infra.errors import NeoMAGIError
from src.infra.logging import setup_logging
from src.session.database import create_db_engine, ensure_schema, make_session_factory
from src.session.manager import SessionManager
from src.tools.builtins import register_builtins
from src.tools.registry import ToolRegistry

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: initialize shared state on startup."""
    setup_logging(json_output=False)

    settings = get_settings()

    # [Decision 0020] DB is mandatory; startup fails if DB/schema unavailable.
    engine = await create_db_engine(settings.database)
    await ensure_schema(engine, settings.database.schema_)
    db_session_factory = make_session_factory(engine)
    logger.info("db_connected")

    session_manager = SessionManager(db_session_factory=db_session_factory)
    model_client = OpenAICompatModelClient(
        api_key=settings.openai.api_key,
        base_url=settings.openai.base_url,
    )

    tool_registry = ToolRegistry()
    register_builtins(tool_registry, settings.workspace_dir)

    agent_loop = AgentLoop(
        model_client=model_client,
        session_manager=session_manager,
        workspace_dir=settings.workspace_dir,
        model=settings.openai.model,
        tool_registry=tool_registry,
    )

    app.state.agent_loop = agent_loop
    app.state.session_manager = session_manager
    logger.info("gateway_started", host=settings.gateway.host, port=settings.gateway.port)

    yield

    # Cleanup
    await engine.dispose()
    logger.info("db_engine_disposed")


app = FastAPI(title="NeoMAGI Gateway", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    logger.info("ws_connected")
    try:
        while True:
            raw = await websocket.receive_text()
            await _handle_rpc_message(websocket, raw)
    except WebSocketDisconnect:
        logger.info("ws_disconnected")


async def _handle_rpc_message(websocket: WebSocket, raw: str) -> None:
    """Parse RPC request, invoke agent, stream response events back."""
    request_id = "unknown"
    try:
        request = parse_rpc_request(raw)
        request_id = request.id

        if request.method == "chat.send":
            await _handle_chat_send(websocket, request_id, request.params)
        elif request.method == "chat.history":
            await _handle_chat_history(websocket, request_id, request.params)
        else:
            error = RPCError(
                id=request_id,
                error=RPCErrorData(
                    code="METHOD_NOT_FOUND",
                    message=f"Unknown method: {request.method}",
                ),
            )
            await websocket.send_text(error.model_dump_json())

    except NeoMAGIError as e:
        logger.warning("request_error", code=e.code, error=str(e), request_id=request_id)
        error = RPCError(
            id=request_id,
            error=RPCErrorData(code=e.code, message=str(e)),
        )
        await websocket.send_text(error.model_dump_json())
    except Exception:
        logger.exception("unhandled_error", request_id=request_id)
        error = RPCError(
            id=request_id,
            error=RPCErrorData(code="INTERNAL_ERROR", message="An internal error occurred"),
        )
        await websocket.send_text(error.model_dump_json())


async def _handle_chat_send(
    websocket: WebSocket, request_id: str, params: dict
) -> None:
    """Handle chat.send: claim session, invoke agent loop, stream events, release."""
    parsed = ChatSendParams.model_validate(params)
    session_manager: SessionManager = websocket.app.state.session_manager
    settings = get_settings()

    # [Decision 0021] Session-level serialization: try-claim before processing
    lock_token = await session_manager.try_claim_session(
        parsed.session_id,
        ttl_seconds=settings.gateway.session_claim_ttl_seconds,
    )
    if lock_token is None:
        error = RPCError(
            id=request_id,
            error=RPCErrorData(
                code="SESSION_BUSY",
                message="Session is being processed by another request. Please try again.",
            ),
        )
        await websocket.send_text(error.model_dump_json())
        return

    try:
        # [Decision 0021] Force-reload session from DB before building prompt.
        # Ensures cross-worker handoff has complete history, not stale local cache.
        await session_manager.load_session_from_db(parsed.session_id, force=True)

        agent_loop: AgentLoop = websocket.app.state.agent_loop
        async for event in agent_loop.handle_message(
            session_id=parsed.session_id,
            content=parsed.content,
            lock_token=lock_token,
        ):
            if isinstance(event, TextChunk):
                chunk = RPCStreamChunk(
                    id=request_id,
                    data=StreamChunkData(content=event.content, done=False),
                )
                await websocket.send_text(chunk.model_dump_json())
            elif isinstance(event, ToolCallInfo):
                tool_msg = RPCToolCall(
                    id=request_id,
                    data=ToolCallData(
                        tool_name=event.tool_name,
                        arguments=event.arguments,
                        call_id=event.call_id,
                    ),
                )
                await websocket.send_text(tool_msg.model_dump_json())

        done_chunk = RPCStreamChunk(
            id=request_id,
            data=StreamChunkData(content="", done=True),
        )
        await websocket.send_text(done_chunk.model_dump_json())
    finally:
        try:
            await session_manager.release_session(parsed.session_id, lock_token)
        except Exception:
            # [Decision 0022] release is best-effort; TTL recovers stale locks.
            # Do NOT re-raise — avoid sending INTERNAL_ERROR after done=true.
            logger.exception(
                "session_release_failed",
                session_id=parsed.session_id,
                msg="Lock will be recovered by TTL expiry",
            )


async def _handle_chat_history(
    websocket: WebSocket, request_id: str, params: dict
) -> None:
    """Handle chat.history: return session message history."""
    parsed = ChatHistoryParams.model_validate(params)
    session_manager: SessionManager = websocket.app.state.session_manager

    # [Decision 0019] chat.history only returns display-safe messages (user/assistant).
    history = await session_manager.get_history_for_display(parsed.session_id)
    response = RPCHistoryResponse(id=request_id, data=RPCHistoryResponseData(messages=history))
    await websocket.send_text(response.model_dump_json())
