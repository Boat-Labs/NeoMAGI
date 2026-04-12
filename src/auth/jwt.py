"""JWT token creation and verification (P2-M3a)."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import jwt


def create_token(
    principal_id: str,
    secret: str,
    expire_hours: int = 24,
) -> tuple[str, datetime]:
    """Create a JWT token for the given principal. Returns (token, expires_at)."""
    expires_at = datetime.now(UTC) + timedelta(hours=expire_hours)
    payload = {
        "sub": principal_id,
        "exp": expires_at,
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    return token, expires_at


def verify_token(token: str, secret: str) -> dict | None:
    """Verify and decode a JWT token. Returns payload dict or None on failure."""
    try:
        return jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.InvalidTokenError:
        return None


def generate_secret() -> str:
    """Generate a random JWT secret for ephemeral use."""
    return secrets.token_hex(32)
