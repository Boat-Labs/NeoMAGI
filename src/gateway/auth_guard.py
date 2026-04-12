"""Session access authorization + ownership stamp (P2-M3a D11)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.infra.errors import GatewayError

if TYPE_CHECKING:
    from src.session.manager import SessionManager


async def authorize_and_stamp_session(
    session_manager: SessionManager,
    session_id: str,
    principal_id: str | None,
    auth_mode: bool,
) -> None:
    """Authorize principal access to session, stamp ownership if needed.

    Raises GatewayError on denial. Delegates DB ops to SessionManager.
    """
    if auth_mode and principal_id is None:
        raise GatewayError(
            "Authentication required for this session",
            code="SESSION_AUTH_REQUIRED",
        )

    stored_principal = await session_manager.get_session_principal(session_id)

    if stored_principal is _SESSION_NOT_FOUND:
        return  # session doesn't exist yet — allow

    if stored_principal is not None:
        if principal_id is None:
            raise GatewayError(
                "Session requires authentication",
                code="SESSION_AUTH_REQUIRED",
            )
        if stored_principal != principal_id:
            raise GatewayError(
                "Session belongs to a different principal",
                code="SESSION_OWNER_MISMATCH",
            )
        return  # same principal — OK

    # stored_principal is None (NULL in DB)
    if principal_id is not None:
        await session_manager.stamp_session_principal(session_id, principal_id)


# Sentinel to distinguish "session not found" from "principal_id is NULL"
_SESSION_NOT_FOUND = object()
