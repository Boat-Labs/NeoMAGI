from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

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
    schema_: str = Field("public", validation_alias="DATABASE_SCHEMA")


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


class Settings(BaseSettings):
    """Root settings composing all sub-configurations."""

    model_config = SettingsConfigDict(extra="ignore")

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    gateway: GatewaySettings = Field(default_factory=GatewaySettings)
    workspace_dir: Path = Path("workspace")


def get_settings() -> Settings:
    """Load and validate settings. Raises ValidationError on missing required fields."""
    return Settings()
