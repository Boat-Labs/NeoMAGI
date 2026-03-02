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
    dm_scope: str = "main"

    @field_validator("default_mode")
    @classmethod
    def _validate_default_mode(cls, v: str) -> str:
        if v != "chat_safe":
            raise ValueError(
                f"SESSION_DEFAULT_MODE must be 'chat_safe' in M1.5 "
                f"(got '{v}'). See ADR 0025."
            )
        return v

    @field_validator("dm_scope")
    @classmethod
    def _validate_dm_scope(cls, v: str) -> str:
        if v != "main":
            raise ValueError(
                f"SESSION_DM_SCOPE must be 'main' in M3 (got '{v}'). "
                "Non-main scopes will be enabled in M4. See ADR 0034."
            )
        return v


class CompactionSettings(BaseSettings):
    """Compaction and token budget settings.

    Phase 1: budget fields only. Phase 2: adds compaction-specific fields.
    Env prefix: COMPACTION_ (stable across all phases).
    """

    model_config = SettingsConfigDict(env_prefix="COMPACTION_")

    # Token budget (Phase 1)
    context_limit: int = 128_000
    warn_ratio: float = 0.80
    compact_ratio: float = 0.90
    reserved_output_tokens: int = 2048
    safety_margin_tokens: int = 1024

    # Compaction (Phase 2)
    min_preserved_turns: int = 8
    flush_timeout_s: float = 30.0
    compact_timeout_s: float = 30.0
    fail_open: bool = True
    max_flush_candidates: int = 20
    max_candidate_text_bytes: int = 2048
    max_compactions_per_request: int = 2
    summary_temperature: float = 0.1
    anchor_retry_enabled: bool = True

    @model_validator(mode="after")
    def _validate(self) -> Self:
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
        if not (0.0 <= self.summary_temperature <= 1.0):
            raise ValueError(
                f"summary_temperature must be in [0.0, 1.0], got {self.summary_temperature}"
            )
        return self


class MemorySettings(BaseSettings):
    """Memory write and retrieval settings. Env vars prefixed with MEMORY_."""

    model_config = SettingsConfigDict(env_prefix="MEMORY_")

    workspace_path: Path = Path("workspace")
    max_daily_note_bytes: int = 32_768  # 32KB per daily note
    daily_notes_load_days: int = 2  # today + yesterday
    daily_notes_max_tokens: int = 4000  # per file injection limit
    flush_min_confidence: float = 0.5  # filter low-confidence candidates
    # Search settings (Phase 2)
    search_default_limit: int = 10
    search_min_score: float = 0.0
    search_result_max_chars: int = 500  # truncation for tool results
    # Memory recall settings (Phase 3)
    memory_recall_max_tokens: int = 2000  # injection limit for recall layer
    memory_recall_min_score: float = 1.0  # BM25/tsvector score threshold
    memory_recall_max_results: int = 5
    # Curation settings (Phase 3)
    curated_max_tokens: int = 4000  # MEMORY.md size limit
    curation_lookback_days: int = 7
    curation_temperature: float = 0.1
    curation_model: str = "gpt-4o-mini"  # offline curation model, independent of provider routing


class TelegramSettings(BaseSettings):
    """Telegram channel settings. Env vars prefixed with TELEGRAM_."""

    model_config = SettingsConfigDict(env_prefix="TELEGRAM_")

    bot_token: str = ""  # empty = channel disabled
    dm_scope: str = "per-channel-peer"
    allowed_user_ids: str = ""  # comma-separated Telegram user ID whitelist
    message_max_length: int = 4096

    @field_validator("dm_scope")
    @classmethod
    def _validate_dm_scope(cls, v: str) -> str:
        allowed = {"per-channel-peer", "per-peer", "main"}
        if v not in allowed:
            msg = f"TELEGRAM_DM_SCOPE must be one of {allowed} (got '{v}')"
            raise ValueError(msg)
        return v


class GeminiSettings(BaseSettings):
    """Gemini API settings via OpenAI-compatible endpoint."""

    model_config = SettingsConfigDict(env_prefix="GEMINI_")

    api_key: str = ""  # empty = provider disabled
    model: str = "gemini-2.5-flash"
    base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"


class ProviderSettings(BaseSettings):
    """Provider routing settings."""

    model_config = SettingsConfigDict(env_prefix="PROVIDER_")

    active: str = "openai"  # fallback when ChatSendParams.provider is not specified

    @field_validator("active")
    @classmethod
    def _validate_active(cls, v: str) -> str:
        allowed = {"openai", "gemini"}
        if v not in allowed:
            msg = f"PROVIDER_ACTIVE must be one of {allowed} (got '{v}')"
            raise ValueError(msg)
        return v


class Settings(BaseSettings):
    """Root settings composing all sub-configurations."""

    model_config = SettingsConfigDict(extra="ignore")

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    gemini: GeminiSettings = Field(default_factory=GeminiSettings)
    provider: ProviderSettings = Field(default_factory=ProviderSettings)
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    session: SessionSettings = Field(default_factory=SessionSettings)
    compaction: CompactionSettings = Field(default_factory=CompactionSettings)
    memory: MemorySettings = Field(default_factory=MemorySettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    workspace_dir: Path = Path("workspace")


def get_settings() -> Settings:
    """Load and validate settings. Raises ValidationError on missing required fields."""
    return Settings()
