"""Authentication configuration settings (P2-M3a)."""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthSettings(BaseSettings):
    """Authentication settings. Env vars prefixed with AUTH_.

    password_hash = None → no-auth mode (anonymous WebSocket, dev/test).
    password_hash = "$2b$..." → auth mode (require login + JWT).
    """

    model_config = SettingsConfigDict(env_prefix="AUTH_")

    password_hash: str | None = None
    jwt_secret: str | None = None
    jwt_expire_hours: int = 24
    owner_name: str = "Owner"

    @field_validator("password_hash")
    @classmethod
    def _validate_password_hash(cls, v: str | None) -> str | None:
        if v is not None and not v.startswith("$2b$"):
            msg = (
                "AUTH_PASSWORD_HASH must be a bcrypt hash ($2b$ prefix). "
                "Generate with: just hash-password"
            )
            raise ValueError(msg)
        return v
