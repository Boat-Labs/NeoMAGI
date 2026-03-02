"""Core dispatch: provider routing → session claim → budget → handle_message → settle → release.

Extracted from app._handle_chat_send to enable reuse by non-WebSocket channels (e.g. Telegram).
SESSION_BUSY and BUDGET_EXCEEDED propagate as GatewayError.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import structlog

from src.agent.events import AgentEvent
from src.agent.provider_registry import AgentLoopRegistry
from src.gateway.budget_gate import BudgetGate
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
    session_claim_ttl_seconds: int = 300,
) -> AsyncIterator[AgentEvent]:
    """Core dispatch: provider → claim → budget → handle_message → settle → release.

    Yields AgentEvent from handle_message. Caller maps events to transport protocol.
    Raises GatewayError for SESSION_BUSY, BUDGET_EXCEEDED, PROVIDER_NOT_AVAILABLE.
    """
    # 1. Provider routing
    try:
        entry = registry.get(provider)
    except KeyError:
        raise GatewayError(
            f"Provider '{provider}' is not available. "
            f"Configured: {registry.available_providers()}",
            code="PROVIDER_NOT_AVAILABLE",
        )

    logger.info(
        "agent_run_provider_bound",
        provider=entry.name,
        model=entry.model,
        source="request" if provider else "default",
    )

    # 2. Session claim
    lock_token = await session_manager.try_claim_session(
        session_id, ttl_seconds=session_claim_ttl_seconds,
    )
    if lock_token is None:
        raise GatewayError(
            "Session is being processed by another request. Please try again.",
            code="SESSION_BUSY",
        )

    reservation = None
    try:
        await session_manager.load_session_from_db(session_id, force=True)

        # 3. Budget reserve
        reservation = await budget_gate.try_reserve(
            provider=entry.name,
            model=entry.model,
            estimated_cost_eur=DEFAULT_RESERVE_EUR,
            session_id=session_id,
            eval_run_id=_extract_eval_run_id(session_id),
        )
        if reservation.denied:
            raise GatewayError(reservation.message, code="BUDGET_EXCEEDED")

        # 4. Handle message
        async for event in entry.agent_loop.handle_message(
            session_id=session_id,
            content=content,
            lock_token=lock_token,
            identity=identity,
            dm_scope=dm_scope,
        ):
            yield event

    finally:
        # 5. Budget settle (best-effort)
        if reservation is not None and not reservation.denied:
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

        # 6. Session release (best-effort, TTL recovers stale locks)
        try:
            await session_manager.release_session(session_id, lock_token)
        except Exception:
            logger.exception(
                "session_release_failed",
                session_id=session_id,
                msg="Lock will be recovered by TTL expiry",
            )
