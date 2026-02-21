from __future__ import annotations

from pathlib import Path
from typing import Self

from dotenv import load_dotenv
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.constants import DB_SCHEMA

# Load .env once at module import — all BaseSettings subclasses will see the env vars
load_dotenv()


class DatabaseSettings(BaseSettings):
    """PostgreSQL connection settings. Env vars prefixed with DATABASE_."""

    model_config = SettingsConfigDict(env_prefix="DATABASE_")

    host: str = "localhost"
    port: int = 5432
    user: str = "postgres"
    password: str = ""
    name: str = "neomagi"
    schema_: str = Field(DB_SCHEMA, validation_alias="DATABASE_SCHEMA")

    @field_validator("schema_")
    @classmethod
    def _validate_schema(cls, v: str) -> str:
        if v != DB_SCHEMA:
            msg = (
                f"DATABASE_SCHEMA must be '{DB_SCHEMA}' "
                f"(got '{v}'). See ADR 0017."
            )
            raise ValueError(msg)
        return v


class OpenAISettings(BaseSettings):
    """OpenAI API settings. Env vars prefixed with OPENAI_."""

    model_config = SettingsConfigDict(env_prefix="OPENAI_")

    api_key: str  # required — fail fast if missing
    model: str = "gpt-4o-mini"
    base_url: str | None = None


class GatewaySettings(BaseSettings):
    """Gateway server settings. Env vars prefixed with GATEWAY_."""

    model_config = SettingsConfigDict(env_prefix="GATEWAY_")

    host: str = "0.0.0.0"
    port: int = 19789
    session_claim_ttl_seconds: int = Field(
        300, gt=0, le=3600,
        validation_alias="GATEWAY_SESSION_CLAIM_TTL_SECONDS",
    )


class SessionSettings(BaseSettings):
    """Session mode settings. Env vars prefixed with SESSION_."""

    model_config = SettingsConfigDict(env_prefix="SESSION_")

    default_mode: str = "chat_safe"

    @field_validator("default_mode")
    @classmethod
    def _validate_default_mode(cls, v: str) -> str:
        if v != "chat_safe":
            raise ValueError(
                f"SESSION_DEFAULT_MODE must be 'chat_safe' in M1.5 "
                f"(got '{v}'). See ADR 0025."
            )
        return v


class CompactionSettings(BaseSettings):
    """Compaction and token budget settings.

    Phase 1: budget fields only. Phase 2: adds compaction-specific fields.
    Env prefix: COMPACTION_ (stable across all phases).
    """

    model_config = SettingsConfigDict(env_prefix="COMPACTION_")

    context_limit: int = 128_000
    warn_ratio: float = 0.80
    compact_ratio: float = 0.90
    reserved_output_tokens: int = 2048
    safety_margin_tokens: int = 1024

    @model_validator(mode="after")
    def _validate_ratios(self) -> Self:
        if not (0 < self.warn_ratio < 1):
            raise ValueError(f"warn_ratio must be in (0, 1), got {self.warn_ratio}")
        if not (0 < self.compact_ratio < 1):
            raise ValueError(f"compact_ratio must be in (0, 1), got {self.compact_ratio}")
        if self.warn_ratio >= self.compact_ratio:
            raise ValueError(
                f"warn_ratio ({self.warn_ratio}) must be less than "
                f"compact_ratio ({self.compact_ratio})"
            )
        usable = self.context_limit - self.reserved_output_tokens - self.safety_margin_tokens
        if usable <= 0:
            raise ValueError(
                f"usable_input_budget must be > 0, got {usable} "
                f"(context_limit={self.context_limit}, "
                f"reserved_output_tokens={self.reserved_output_tokens}, "
                f"safety_margin_tokens={self.safety_margin_tokens})"
            )
        return self


class Settings(BaseSettings):
    """Root settings composing all sub-configurations."""

    model_config = SettingsConfigDict(extra="ignore")

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    session: SessionSettings = Field(default_factory=SessionSettings)
    compaction: CompactionSettings = Field(default_factory=CompactionSettings)
    workspace_dir: Path = Path("workspace")


def get_settings() -> Settings:
    """Load and validate settings. Raises ValidationError on missing required fields."""
    return Settings()
