from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
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
    RPCSessionModeResponse,
    RPCStreamChunk,
    RPCToolCall,
    RPCToolDenied,
    SessionModeData,
    SessionSetModeParams,
    StreamChunkData,
    ToolCallData,
    ToolDeniedData,
    parse_rpc_request,
)
from src.infra.errors import GatewayError, NeoMAGIError
from src.infra.health import CheckResult, CheckStatus, ComponentHealthTracker, PreflightReport
from src.infra.logging import setup_logging
from src.infra.preflight import run_preflight, run_readiness_checks
from src.memory.indexer import MemoryIndexer
from src.memory.searcher import MemorySearcher
from src.memory.writer import MemoryWriter
from src.session.database import create_db_engine, ensure_schema, make_session_factory
from src.session.manager import SessionManager
from src.tools.base import ToolMode
from src.tools.builtins import register_builtins
from src.tools.registry import ToolRegistry

logger = structlog.get_logger()

_TELEGRAM_SHUTDOWN_TIMEOUT_S = 3.0


def _make_polling_done_callback(
    tracker: ComponentHealthTracker,
) -> Callable[[asyncio.Task], None]:
    """Create callback for telegram polling task: update tracker + fail-fast on fatal error."""

    def _on_polling_done(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            tracker.record_telegram_failure(str(exc))
            logger.error("telegram_polling_fatal", error=str(exc))
            # fail-fast: personal agent should not silently degrade
            os.kill(os.getpid(), signal.SIGTERM)

    return _on_polling_done


# Backward-compat export for tests/import sites that reference the old symbol directly.
_on_polling_done = _make_polling_done_callback(ComponentHealthTracker())


async def _shutdown_telegram(
    telegram_adapter: object | None,
    polling_task: asyncio.Task | None,
    *,
    timeout_s: float = _TELEGRAM_SHUTDOWN_TIMEOUT_S,
) -> None:
    """Best-effort Telegram shutdown that won't block process exit indefinitely."""
    if polling_task and not polling_task.done():
        polling_task.cancel()
        try:
            await asyncio.wait_for(polling_task, timeout=timeout_s)
        except asyncio.CancelledError:
            pass
        except TimeoutError:
            logger.warning("telegram_polling_cancel_timeout", timeout_s=timeout_s)
        except Exception:
            logger.exception("telegram_polling_cancel_failed")

    if telegram_adapter:
        try:
            await asyncio.wait_for(telegram_adapter.stop(), timeout=timeout_s)
        except TimeoutError:
            logger.warning("telegram_adapter_stop_timeout", timeout_s=timeout_s)
        except Exception:
            logger.exception("telegram_adapter_stop_failed")


async def _init_database(settings):
    """Initialize database engine, schema, and session factory."""
    engine = await create_db_engine(settings.database)
    await ensure_schema(engine, settings.database.schema_)
    db_session_factory = make_session_factory(engine)
    logger.info("db_connected")
    return engine, db_session_factory


async def _run_startup_preflight(app, settings, engine):
    """Run preflight checks and fail-fast if any check fails."""
    preflight_report = await run_preflight(settings, engine)
    app.state.preflight_report = preflight_report
    logger.info("preflight_complete", passed=preflight_report.passed)
    if not preflight_report.passed:
        failed = [c for c in preflight_report.checks if c.status == CheckStatus.FAIL]
        raise RuntimeError(
            f"Preflight failed ({len(failed)} check(s)): "
            + "; ".join(f"{c.name}: {c.evidence}" for c in failed)
        )


def _build_governance_engine(
    db_session_factory,
    evolution_engine,
    skill_store,
    tool_registry,
    *,
    procedure_registries=None,
    procedure_governance_store=None,
    procedure_store=None,
):
    """Build governance engine with all onboarded adapters.

    Returns ``(engine, wrapper_tool_store)`` so the caller can restore
    active wrappers at startup.
    """
    from src.growth.adapters.skill import SkillGovernedObjectAdapter
    from src.growth.adapters.soul import SoulGovernedObjectAdapter
    from src.growth.adapters.wrapper_tool import WrapperToolGovernedObjectAdapter
    from src.growth.engine import GrowthGovernanceEngine
    from src.growth.policies import PolicyRegistry
    from src.growth.types import GrowthObjectKind
    from src.wrappers.store import WrapperToolStore

    soul_adapter = SoulGovernedObjectAdapter(evolution_engine)
    skill_adapter = SkillGovernedObjectAdapter(skill_store)
    wrapper_tool_store = WrapperToolStore(db_session_factory)
    wrapper_tool_adapter = WrapperToolGovernedObjectAdapter(
        wrapper_tool_store, tool_registry
    )

    adapters: dict = {
        GrowthObjectKind.soul: soul_adapter,
        GrowthObjectKind.skill_spec: skill_adapter,
        GrowthObjectKind.wrapper_tool: wrapper_tool_adapter,
    }

    if procedure_registries and procedure_governance_store and procedure_store:
        from src.growth.adapters.procedure_spec import ProcedureSpecGovernedObjectAdapter

        procedure_spec_adapter = ProcedureSpecGovernedObjectAdapter(
            governance_store=procedure_governance_store,
            spec_registry=procedure_registries.spec_registry,
            tool_registry=tool_registry,
            context_registry=procedure_registries.context_registry,
            guard_registry=procedure_registries.guard_registry,
            procedure_store=procedure_store,
        )
        adapters[GrowthObjectKind.procedure_spec] = procedure_spec_adapter

    policy_registry = PolicyRegistry()
    engine = GrowthGovernanceEngine(
        adapters=adapters,
        policy_registry=policy_registry,
    )
    return engine, wrapper_tool_store


async def _restore_active_wrappers(wrapper_tool_store, tool_registry) -> int:
    """Restore active wrapper tools from DB into ToolRegistry at startup.

    Returns the number of wrappers restored.  Logs and skips any wrapper
    whose factory fails to resolve (non-fatal: the DB record stays active
    so an operator can investigate).
    """
    from src.growth.adapters.wrapper_tool import _resolve_and_register

    specs = await wrapper_tool_store.get_active()
    restored = 0
    for spec in specs:
        try:
            _resolve_and_register(spec, tool_registry)
            restored += 1
        except Exception:
            logger.exception(
                "wrapper_tool_restore_failed",
                wrapper_tool_id=spec.id,
                implementation_ref=spec.implementation_ref,
            )
    if restored:
        logger.info("wrapper_tools_restored", count=restored, total=len(specs))
    return restored


async def _build_memory_and_tools(settings, db_session_factory):
    """Build memory stack + tool registry + skill runtime (incl. learner)."""
    memory_indexer = MemoryIndexer(db_session_factory, settings.memory)
    memory_searcher = MemorySearcher(db_session_factory, settings.memory)
    memory_writer = MemoryWriter(settings.workspace_dir, settings.memory, indexer=memory_indexer)

    from src.memory.evolution import EvolutionEngine

    evolution_engine = EvolutionEngine(db_session_factory, settings.workspace_dir, settings.memory)

    tool_registry = ToolRegistry()
    register_builtins(
        tool_registry, settings.workspace_dir,
        memory_searcher=memory_searcher, memory_writer=memory_writer,
        evolution_engine=evolution_engine,
    )

    from src.skills.learner import SkillLearner
    from src.skills.projector import SkillProjector
    from src.skills.resolver import SkillResolver
    from src.skills.store import SkillStore

    skill_store = SkillStore(db_session_factory)
    skill_resolver = SkillResolver(registry=skill_store)
    skill_projector = SkillProjector()

    # 1. Construct procedure registries (shared bundle)
    procedure_registries = _build_procedure_registries(tool_registry)

    # 2. Register procedure-only tools (BEFORE restore — restore needs tool validation)
    _register_procedure_tools(tool_registry)

    # 3. Construct procedure governance store + procedure store
    from src.procedures.governance_store import ProcedureSpecGovernanceStore
    from src.procedures.store import ProcedureStore

    procedure_governance_store = ProcedureSpecGovernanceStore(db_session_factory)
    procedure_store = ProcedureStore(db_session_factory)

    # 4. Build governance engine (with full procedure bundle)
    governance_engine, wrapper_tool_store = _build_governance_engine(
        db_session_factory, evolution_engine, skill_store, tool_registry,
        procedure_registries=procedure_registries,
        procedure_governance_store=procedure_governance_store,
        procedure_store=procedure_store,
    )
    skill_learner = SkillLearner(skill_store, governance_engine)

    # 5. Restore active wrappers
    await _restore_active_wrappers(wrapper_tool_store, tool_registry)

    # 6. Restore active procedure specs (from governance store → spec_registry)
    await _restore_active_procedure_specs(
        procedure_governance_store, procedure_registries.spec_registry,
    )

    # 7. Construct procedure runtime (using already-restored registries)
    procedure_runtime = _build_procedure_runtime_from_registries(
        procedure_registries, db_session_factory, tool_registry, procedure_store,
    )

    return (
        memory_searcher, evolution_engine, tool_registry,
        skill_resolver, skill_projector, skill_learner,
        procedure_runtime,
    )


_ProcedureRegistries = None  # populated lazily


def _build_procedure_registries(tool_registry):
    """Build shared procedure registries as a named bundle."""
    from collections import namedtuple

    from src.procedures.registry import (
        ProcedureContextRegistry,
        ProcedureGuardRegistry,
        ProcedureSpecRegistry,
    )

    global _ProcedureRegistries  # noqa: PLW0603
    if _ProcedureRegistries is None:
        _ProcedureRegistries = namedtuple(
            "ProcedureRegistries", ["spec_registry", "context_registry", "guard_registry"]
        )

    context_registry = ProcedureContextRegistry()
    guard_registry = ProcedureGuardRegistry()
    spec_registry = ProcedureSpecRegistry(tool_registry, context_registry, guard_registry)
    return _ProcedureRegistries(spec_registry, context_registry, guard_registry)


async def _restore_active_procedure_specs(governance_store, spec_registry) -> int:
    """Restore active procedure specs from DB into ProcedureSpecRegistry at startup.

    Returns the number of specs restored.
    """
    from src.procedures.types import ProcedureSpec

    payloads = await governance_store.list_active()
    restored = 0
    for payload in payloads:
        try:
            spec = ProcedureSpec.model_validate(payload)
            if spec_registry.get(spec.id) is None:
                spec_registry.register(spec)
            restored += 1
        except Exception:
            spec_id = payload.get("id", "<unknown>")
            logger.exception("procedure_spec_restore_failed", procedure_spec_id=spec_id)
    if restored:
        logger.info("procedure_specs_restored", count=restored, total=len(payloads))
    return restored


def _build_procedure_runtime_from_registries(
    registries, db_session_factory, tool_registry, procedure_store=None,
):
    """Build ProcedureRuntime using pre-constructed registries.

    The registries are shared with the governance adapter so that
    apply/rollback mutations are visible to the runtime.
    """
    from src.procedures.runtime import ProcedureRuntime
    from src.procedures.store import ProcedureStore

    store = procedure_store or ProcedureStore(db_session_factory)
    return ProcedureRuntime(
        registries.spec_registry,
        registries.context_registry,
        registries.guard_registry,
        store,
        tool_registry,
    )


def _register_procedure_tools(tool_registry):
    """Register DelegationTool / ReviewTool / PublishTool (P2-M2b D7).

    These are stateless shells with allowed_modes=frozenset() (not ambient).
    They read deps from ProcedureActionDeps at execution time (D8).
    """
    from src.procedures.delegation import DelegationTool
    from src.procedures.publish import PublishTool
    from src.procedures.reviewer import ReviewTool

    for tool_cls in (DelegationTool, ReviewTool, PublishTool):
        tool = tool_cls(tool_registry) if tool_cls is DelegationTool else tool_cls()
        if tool_registry.get(tool.name) is None:
            tool_registry.register(tool)


def _build_provider_registry(settings, session_manager, memory_searcher,
                             evolution_engine, tool_registry, health_tracker,
                             skill_resolver=None, skill_projector=None,
                             skill_learner=None, procedure_runtime=None):
    """Register OpenAI + optional Gemini providers."""
    def _make_agent_loop(client: OpenAICompatModelClient, model: str) -> AgentLoop:
        return AgentLoop(
            model_client=client, session_manager=session_manager,
            workspace_dir=settings.workspace_dir, model=model,
            tool_registry=tool_registry, compaction_settings=settings.compaction,
            session_settings=settings.session, memory_settings=settings.memory,
            memory_searcher=memory_searcher, evolution_engine=evolution_engine,
            skill_resolver=skill_resolver, skill_projector=skill_projector,
            skill_learner=skill_learner,
            procedure_runtime=procedure_runtime,
        )

    registry = AgentLoopRegistry(default_provider=settings.provider.active)
    openai_client = OpenAICompatModelClient(
        api_key=settings.openai.api_key, base_url=settings.openai.base_url,
        health_tracker=health_tracker, provider_name="openai",
    )
    registry.register("openai", _make_agent_loop(openai_client, settings.openai.model),
                       settings.openai.model)

    if settings.gemini.api_key:
        gemini_client = OpenAICompatModelClient(
            api_key=settings.gemini.api_key, base_url=settings.gemini.base_url,
            health_tracker=health_tracker, provider_name="gemini",
        )
        registry.register("gemini", _make_agent_loop(gemini_client, settings.gemini.model),
                           settings.gemini.model)
        logger.info("gemini_provider_registered", model=settings.gemini.model)
    return registry


async def _start_telegram(settings, registry, session_manager, budget_gate,
                          health_tracker):
    """Start optional Telegram adapter. Returns (adapter, polling_task)."""
    if not settings.telegram.bot_token:
        return None, None
    from src.channels.telegram import TelegramAdapter

    adapter = TelegramAdapter(
        bot_token=settings.telegram.bot_token,
        telegram_settings=settings.telegram, registry=registry,
        session_manager=session_manager, budget_gate=budget_gate,
        gateway_settings=settings.gateway,
    )
    await adapter.check_ready()
    task = asyncio.create_task(adapter.start_polling(), name="telegram_polling")
    task.add_done_callback(_make_polling_done_callback(health_tracker))
    logger.info("telegram_adapter_started", username=adapter._bot_username)
    return adapter, task


def _load_settings():
    """Load and validate settings."""
    return get_settings()


def _bind_app_state(app, *, registry, session_manager, budget_gate,
                    engine, settings, health_tracker):
    app.state.agent_loop_registry = registry
    app.state.agent_loop = registry.get().agent_loop
    app.state.session_manager = session_manager
    app.state.budget_gate = budget_gate
    app.state.db_engine = engine
    app.state.settings = settings
    app.state.health_tracker = health_tracker


def _log_settings_errors(exc: ValidationError) -> None:
    for err in exc.errors():
        logger.error(
            "settings_validation_error",
            field=".".join(str(loc) for loc in err["loc"]),
            error_type=err["type"], message=err["msg"],
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: initialize shared state on startup."""
    setup_logging(json_output=False)
    try:
        settings = _load_settings()
    except ValidationError as e:
        _log_settings_errors(e)
        raise

    engine, db_session_factory = await _init_database(settings)
    await _run_startup_preflight(app, settings, engine)

    session_manager = SessionManager(
        db_session_factory=db_session_factory,
        default_mode=ToolMode(settings.session.default_mode),
    )
    (
        memory_searcher, evolution_engine, tool_registry,
        skill_resolver, skill_projector, skill_learner,
        procedure_runtime,
    ) = await _build_memory_and_tools(settings, db_session_factory)
    health_tracker = ComponentHealthTracker()
    registry = _build_provider_registry(
        settings, session_manager, memory_searcher,
        evolution_engine, tool_registry, health_tracker,
        skill_resolver=skill_resolver, skill_projector=skill_projector,
        skill_learner=skill_learner,
        procedure_runtime=procedure_runtime,
    )
    budget_gate = BudgetGate(engine, schema=settings.database.schema_)
    _bind_app_state(app, registry=registry, session_manager=session_manager,
                    budget_gate=budget_gate, engine=engine, settings=settings,
                    health_tracker=health_tracker)
    logger.info(
        "gateway_started", host=settings.gateway.host, port=settings.gateway.port,
        providers=registry.available_providers(), default_provider=registry.default_name,
    )
    telegram_adapter, polling_task = await _start_telegram(
        settings, registry, session_manager, budget_gate, health_tracker,
    )

    yield

    await _shutdown_telegram(telegram_adapter, polling_task)
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


@app.get("/health/live")
async def health_live() -> dict[str, str]:
    return {"status": "alive"}


def _checks_to_dict(checks: list[CheckResult]) -> dict:
    return {c.name: {"status": c.status.value, "evidence": c.evidence} for c in checks}


def _collect_component_checks(tracker: ComponentHealthTracker) -> list[CheckResult]:
    """Layer 3: in-process component health checks."""
    checks: list[CheckResult] = []
    if not tracker.telegram_healthy:
        checks.append(CheckResult(
            name="telegram_runtime", status=CheckStatus.FAIL,
            evidence=f"Polling fatal: {tracker.telegram_error}",
            impact="Telegram channel down", next_action="Restart service",
        ))
    for prov_name, fail_count in tracker.unhealthy_providers().items():
        checks.append(CheckResult(
            name=f"provider_runtime_{prov_name}", status=CheckStatus.FAIL,
            evidence=f"{fail_count} consecutive LLM failures ({prov_name})",
            impact=f"LLM requests to {prov_name} failing",
            next_action=f"Check {prov_name} provider status",
        ))
    return checks


@app.get("/health/ready")
async def health_ready(request: Request) -> dict:
    """Three-layer readiness: real-time checks + startup latched + in-process state."""
    startup_report: PreflightReport | None = getattr(
        request.app.state, "preflight_report", None,
    )
    if startup_report is None or not startup_report.passed:
        checks = _checks_to_dict(startup_report.checks) if startup_report else {}
        return {"status": "not_ready", "checks": checks}

    settings = request.app.state.settings
    engine = request.app.state.db_engine
    tracker: ComponentHealthTracker = request.app.state.health_tracker

    realtime = await run_readiness_checks(settings, engine)
    startup_checks = [
        c for c in startup_report.checks if c.name in ("active_provider", "soul_reconcile")
    ]
    component_checks = _collect_component_checks(tracker)

    all_checks = realtime.checks + startup_checks + component_checks
    has_fail = any(c.status == CheckStatus.FAIL for c in all_checks)
    return {"status": "not_ready" if has_fail else "ready", "checks": _checks_to_dict(all_checks)}


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


_RPC_HANDLERS: dict[str, object] = {}  # populated after handler definitions


async def _handle_rpc_message(websocket: WebSocket, raw: str) -> None:
    """Parse RPC request, invoke agent, stream response events back."""
    request_id = "unknown"
    try:
        request = parse_rpc_request(raw)
        request_id = request.id
        handler = _RPC_HANDLERS.get(request.method)
        if handler is not None:
            await handler(websocket, request_id, request.params)
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


def _event_to_rpc(event: TextChunk | ToolDenied | ToolCallInfo, request_id: str):
    """Map a dispatch event to an RPC message, or return None for unknown types."""
    if isinstance(event, TextChunk):
        return RPCStreamChunk(
            id=request_id, data=StreamChunkData(content=event.content, done=False),
        )
    if isinstance(event, ToolDenied):
        return RPCToolDenied(
            id=request_id,
            data=ToolDeniedData(
                call_id=event.call_id, tool_name=event.tool_name,
                mode=event.mode, error_code=event.error_code,
                message=event.message, next_action=event.next_action,
            ),
        )
    if isinstance(event, ToolCallInfo):
        return RPCToolCall(
            id=request_id,
            data=ToolCallData(
                tool_name=event.tool_name, arguments=event.arguments,
                call_id=event.call_id,
            ),
        )
    return None


async def _handle_chat_send(websocket: WebSocket, request_id: str, params: dict) -> None:
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
        registry=registry, session_manager=session_manager,
        budget_gate=budget_gate, session_id=parsed.session_id,
        content=parsed.content, provider=parsed.provider,
        session_claim_ttl_seconds=settings.gateway.session_claim_ttl_seconds,
    ):
        rpc_msg = _event_to_rpc(event, request_id)
        if rpc_msg is not None:
            await websocket.send_text(rpc_msg.model_dump_json())

    done_chunk = RPCStreamChunk(id=request_id, data=StreamChunkData(content="", done=True))
    await websocket.send_text(done_chunk.model_dump_json())


async def _handle_chat_history(websocket: WebSocket, request_id: str, params: dict) -> None:
    """Handle chat.history: return session message history."""
    try:
        parsed = ChatHistoryParams.model_validate(params)
    except ValidationError as e:
        raise GatewayError(str(e), code="INVALID_PARAMS") from e
    session_manager: SessionManager = websocket.app.state.session_manager

    # [Decision 0019] chat.history only returns display-safe messages (user/assistant).
    history = await session_manager.get_history_for_display(parsed.session_id)
    response = RPCHistoryResponse(id=request_id, data=RPCHistoryResponseData(messages=history))
    await websocket.send_text(response.model_dump_json())


async def _handle_session_set_mode(websocket: WebSocket, request_id: str, params: dict) -> None:
    """Handle session.set_mode: explicitly switch session mode (ADR 0058)."""
    try:
        parsed = SessionSetModeParams.model_validate(params)
    except ValidationError as e:
        raise GatewayError(str(e), code="INVALID_PARAMS") from e

    session_manager: SessionManager = websocket.app.state.session_manager

    try:
        effective_mode = await session_manager.set_mode(parsed.session_id, ToolMode(parsed.mode))
    except ValueError as e:
        raise GatewayError(str(e), code="INVALID_PARAMS") from e

    response = RPCSessionModeResponse(
        id=request_id,
        data=SessionModeData(session_id=parsed.session_id, mode=effective_mode.value),
    )
    await websocket.send_text(response.model_dump_json())


# Populate RPC handler dispatch table after all handlers are defined.
_RPC_HANDLERS.update({
    "chat.send": _handle_chat_send,
    "chat.history": _handle_chat_history,
    "session.set_mode": _handle_session_set_mode,
})
