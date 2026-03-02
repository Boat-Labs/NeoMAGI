from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from src.agent.agent import AgentLoop
from src.agent.events import TextChunk, ToolCallInfo, ToolDenied
from src.agent.model_client import OpenAICompatModelClient
from src.agent.provider_registry import AgentLoopRegistry
from src.config.settings import get_settings
from src.gateway.budget_gate import BudgetGate
from src.gateway.dispatch import dispatch_chat
from src.gateway.protocol import (
    ChatHistoryParams,
    ChatSendParams,
    RPCError,
    RPCErrorData,
    RPCHistoryResponse,
    RPCHistoryResponseData,
    RPCStreamChunk,
    RPCToolCall,
    RPCToolDenied,
    StreamChunkData,
    ToolCallData,
    ToolDeniedData,
    parse_rpc_request,
)
from src.infra.errors import GatewayError, NeoMAGIError
from src.infra.logging import setup_logging
from src.memory.evolution import EvolutionEngine
from src.memory.indexer import MemoryIndexer
from src.memory.searcher import MemorySearcher
from src.memory.writer import MemoryWriter
from src.session.database import create_db_engine, ensure_schema, make_session_factory
from src.session.manager import SessionManager
from src.tools.base import ToolMode
from src.tools.builtins import register_builtins
from src.tools.registry import ToolRegistry

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: initialize shared state on startup."""
    setup_logging(json_output=False)

    settings = get_settings()

    # [ADR 0037] workspace_path single source of truth: fail-fast if inconsistent
    # Use .resolve() to normalize symlinks, .., etc. before comparison
    if settings.memory.workspace_path.resolve() != settings.workspace_dir.resolve():
        raise RuntimeError(
            f"workspace_path mismatch: Settings.workspace_dir={settings.workspace_dir}, "
            f"MemorySettings.workspace_path={settings.memory.workspace_path}. "
            "See ADR 0037."
        )

    # [Decision 0020] DB is mandatory; startup fails if DB/schema unavailable.
    engine = await create_db_engine(settings.database)
    await ensure_schema(engine, settings.database.schema_)
    db_session_factory = make_session_factory(engine)
    logger.info("db_connected")

    # Provider-agnostic shared deps
    session_manager = SessionManager(
        db_session_factory=db_session_factory,
        default_mode=ToolMode(settings.session.default_mode),
    )

    # Build Memory dependency chain
    memory_indexer = MemoryIndexer(db_session_factory, settings.memory)
    memory_searcher = MemorySearcher(db_session_factory, settings.memory)
    memory_writer = MemoryWriter(settings.workspace_dir, settings.memory, indexer=memory_indexer)
    evolution_engine = EvolutionEngine(db_session_factory, settings.workspace_dir, settings.memory)

    # [ADR 0036] Startup reconciliation: DB is SSOT, SOUL.md is projection
    await evolution_engine.reconcile_soul_projection()

    tool_registry = ToolRegistry()
    register_builtins(
        tool_registry,
        settings.workspace_dir,
        memory_searcher=memory_searcher,
        memory_writer=memory_writer,
        evolution_engine=evolution_engine,
    )

    # Helper: create AgentLoop for a given provider
    def _make_agent_loop(client: OpenAICompatModelClient, model: str) -> AgentLoop:
        return AgentLoop(
            model_client=client,
            session_manager=session_manager,
            workspace_dir=settings.workspace_dir,
            model=model,
            tool_registry=tool_registry,
            compaction_settings=settings.compaction,
            session_settings=settings.session,
            memory_settings=settings.memory,
            memory_searcher=memory_searcher,
            evolution_engine=evolution_engine,
        )

    # Build registry
    registry = AgentLoopRegistry(default_provider=settings.provider.active)

    # OpenAI (always registered, api_key is required)
    openai_client = OpenAICompatModelClient(
        api_key=settings.openai.api_key,
        base_url=settings.openai.base_url,
    )
    registry.register(
        "openai", _make_agent_loop(openai_client, settings.openai.model), settings.openai.model,
    )

    # Gemini (only when api_key is non-empty)
    if settings.gemini.api_key:
        gemini_client = OpenAICompatModelClient(
            api_key=settings.gemini.api_key,
            base_url=settings.gemini.base_url,
        )
        registry.register(
            "gemini",
            _make_agent_loop(gemini_client, settings.gemini.model),
            settings.gemini.model,
        )
        logger.info("gemini_provider_registered", model=settings.gemini.model)

    # Validate active provider is registered (fail-fast)
    try:
        registry.get()
    except KeyError as e:
        raise RuntimeError(
            f"Active provider '{settings.provider.active}' is not configured. "
            "Check API key settings."
        ) from e

    # Budget gate (ADR 0041)
    budget_gate = BudgetGate(engine, schema=settings.database.schema_)

    app.state.agent_loop_registry = registry
    # Backward compat: keep agent_loop for any code that references it directly
    app.state.agent_loop = registry.get().agent_loop
    app.state.session_manager = session_manager
    app.state.budget_gate = budget_gate
    logger.info(
        "gateway_started",
        host=settings.gateway.host,
        port=settings.gateway.port,
        providers=registry.available_providers(),
        default_provider=registry.default_name,
    )

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
    """Handle chat.send: delegate to dispatch_chat, stream events over WebSocket."""
    try:
        parsed = ChatSendParams.model_validate(params)
    except ValidationError as e:
        raise GatewayError(str(e), code="INVALID_PARAMS") from e

    registry: AgentLoopRegistry = websocket.app.state.agent_loop_registry
    session_manager: SessionManager = websocket.app.state.session_manager
    budget_gate: BudgetGate = websocket.app.state.budget_gate
    settings = get_settings()

    async for event in dispatch_chat(
        registry=registry,
        session_manager=session_manager,
        budget_gate=budget_gate,
        session_id=parsed.session_id,
        content=parsed.content,
        provider=parsed.provider,
        session_claim_ttl_seconds=settings.gateway.session_claim_ttl_seconds,
    ):
        if isinstance(event, TextChunk):
            chunk = RPCStreamChunk(
                id=request_id,
                data=StreamChunkData(content=event.content, done=False),
            )
            await websocket.send_text(chunk.model_dump_json())
        elif isinstance(event, ToolDenied):
            denied_msg = RPCToolDenied(
                id=request_id,
                data=ToolDeniedData(
                    call_id=event.call_id,
                    tool_name=event.tool_name,
                    mode=event.mode,
                    error_code=event.error_code,
                    message=event.message,
                    next_action=event.next_action,
                ),
            )
            await websocket.send_text(denied_msg.model_dump_json())
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
