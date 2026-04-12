"""Core dispatch: provider routing → session claim → budget → handle_message → settle → release.

Extracted from app._handle_chat_send to enable reuse by non-WebSocket channels (e.g. Telegram).
SESSION_BUSY and BUDGET_EXCEEDED propagate as GatewayError.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog

from src.agent.events import AgentEvent
from src.agent.provider_registry import AgentLoopRegistry, ProviderEntry
from src.gateway.budget_gate import BudgetGate, Reservation
from src.infra.errors import GatewayError
from src.session.manager import SessionManager
from src.session.scope_resolver import SessionIdentity

logger = structlog.get_logger()

_EVAL_SESSION_PREFIX = "m6_eval_"

# [ADR 0041] Phase 1: fixed per-request cost estimate.
DEFAULT_RESERVE_EUR: float = 0.05


def _extract_eval_run_id(session_id: str) -> str:
    """Derive eval_run_id from session_id prefix convention.

    Eval script uses session_id = "m6_eval_{provider}_{task}_{timestamp}".
    Timestamp is always the last '_'-separated segment (numeric epoch).
    Provider is always the 3rd segment (index 2).
    Extract "m6_eval_{provider}_{timestamp}" as eval_run_id.
    Online requests (session_id = "main" or other) return empty string.
    """
    if not session_id.startswith(_EVAL_SESSION_PREFIX):
        return ""
    parts = session_id.split("_")
    if len(parts) >= 5:
        provider = parts[2]
        timestamp = parts[-1]
        return f"m6_eval_{provider}_{timestamp}"
    return session_id  # fallback: use full session_id as run_id


def _bind_provider(registry: AgentLoopRegistry, provider: str | None) -> ProviderEntry:
    try:
        entry = registry.get(provider)
    except KeyError:
        raise GatewayError(
            f"Provider '{provider}' is not available. Configured: {registry.available_providers()}",
            code="PROVIDER_NOT_AVAILABLE",
        )

    logger.info(
        "agent_run_provider_bound",
        provider=entry.name,
        model=entry.model,
        source="request" if provider else "default",
    )
    return entry


async def _claim_session_or_raise(
    session_manager: SessionManager,
    session_id: str,
    *,
    ttl_seconds: int,
    principal_id: str | None = None,
    auth_mode: bool = False,
) -> str:
    """Claim session with principal ownership (P2-M3a D7)."""
    claim = await session_manager.claim_session_for_principal(
        session_id,
        principal_id=principal_id,
        auth_mode=auth_mode,
        ttl_seconds=ttl_seconds,
    )
    if claim.lock_token is None:
        code = claim.error_code or "SESSION_BUSY"
        messages = {
            "SESSION_BUSY": "Session is being processed by another request. Please try again.",
            "SESSION_AUTH_REQUIRED": "Session requires authentication.",
            "SESSION_OWNER_MISMATCH": "Session belongs to a different principal.",
        }
        raise GatewayError(messages.get(code, "Session claim failed"), code=code)
    return claim.lock_token


async def _reserve_budget_or_raise(
    budget_gate: BudgetGate,
    entry: ProviderEntry,
    *,
    session_id: str,
) -> Reservation:
    reservation = await budget_gate.try_reserve(
        provider=entry.name,
        model=entry.model,
        estimated_cost_eur=DEFAULT_RESERVE_EUR,
        session_id=session_id,
        eval_run_id=_extract_eval_run_id(session_id),
    )
    if reservation.denied:
        raise GatewayError(reservation.message, code="BUDGET_EXCEEDED")
    return reservation


async def _yield_agent_events(
    entry: ProviderEntry,
    *,
    session_id: str,
    content: str,
    lock_token: str,
    identity: SessionIdentity | None,
    dm_scope: str | None,
) -> AsyncIterator[AgentEvent]:
    async for event in entry.agent_loop.handle_message(
        session_id=session_id,
        content=content,
        lock_token=lock_token,
        identity=identity,
        dm_scope=dm_scope,
    ):
        yield event


def _should_settle(reservation: Reservation | None) -> bool:
    return reservation is not None and not reservation.denied


async def _settle_reservation(
    budget_gate: BudgetGate,
    reservation: Reservation | None,
    *,
    entry: ProviderEntry,
    session_id: str,
) -> None:
    if not _should_settle(reservation):
        return
    try:
        await budget_gate.settle(
            reservation_id=reservation.reservation_id,
            actual_cost_eur=DEFAULT_RESERVE_EUR,
        )
    except Exception:
        logger.exception(
            "budget_settle_failed",
            reservation_id=reservation.reservation_id,
            session_id=session_id,
            provider=entry.name,
            model=entry.model,
        )


async def _release_session(
    session_manager: SessionManager,
    *,
    session_id: str,
    lock_token: str,
) -> None:
    try:
        await session_manager.release_session(session_id, lock_token)
    except Exception:
        logger.exception(
            "session_release_failed",
            session_id=session_id,
            msg="Lock will be recovered by TTL expiry",
        )


async def dispatch_chat(
    *,
    registry: AgentLoopRegistry,
    session_manager: SessionManager,
    budget_gate: BudgetGate,
    session_id: str,
    content: str,
    provider: str | None = None,
    identity: SessionIdentity | None = None,
    dm_scope: str | None = None,
    auth_mode: bool = False,
    session_claim_ttl_seconds: int = 300,
) -> AsyncIterator[AgentEvent]:
    """Core dispatch: provider → claim → budget → handle_message → settle → release.

    Yields AgentEvent from handle_message. Caller maps events to transport protocol.
    Raises GatewayError for SESSION_BUSY, BUDGET_EXCEEDED, PROVIDER_NOT_AVAILABLE,
    SESSION_AUTH_REQUIRED, SESSION_OWNER_MISMATCH.
    """
    entry = _bind_provider(registry, provider)
    principal_id = identity.principal_id if identity else None
    lock_token = await _claim_session_or_raise(
        session_manager,
        session_id,
        ttl_seconds=session_claim_ttl_seconds,
        principal_id=principal_id,
        auth_mode=auth_mode,
    )

    reservation = None
    try:
        await session_manager.load_session_from_db(session_id, force=True)
        reservation = await _reserve_budget_or_raise(budget_gate, entry, session_id=session_id)
        async for event in _yield_agent_events(
            entry, session_id=session_id, content=content, lock_token=lock_token,
            identity=identity, dm_scope=dm_scope,
        ):
            yield event

    finally:
        await _settle_reservation(budget_gate, reservation, entry=entry, session_id=session_id)
        await _release_session(session_manager, session_id=session_id, lock_token=lock_token)
