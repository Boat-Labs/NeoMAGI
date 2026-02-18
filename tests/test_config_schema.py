"""Tests for F1: unified DB_SCHEMA constant, validator, and fail-fast."""

from __future__ import annotations

import inspect

import pytest

from src.constants import DB_SCHEMA


class TestDBSchemaConstant:
    """Verify DB_SCHEMA is the single source of truth across all modules."""

    def test_default_schema_value(self):
        assert DB_SCHEMA == "neomagi"

    def test_models_session_schema(self):
        from src.session.models import SessionRecord

        assert SessionRecord.__table_args__["schema"] == DB_SCHEMA

    def test_models_message_schema(self):
        from src.session.models import MessageRecord

        assert MessageRecord.__table_args__["schema"] == DB_SCHEMA

    def test_settings_default_matches_constant(self):
        from src.config.settings import DatabaseSettings

        field_info = DatabaseSettings.model_fields["schema_"]
        assert field_info.default == DB_SCHEMA

    def test_ensure_schema_default_matches_constant(self):
        from src.session.database import ensure_schema

        sig = inspect.signature(ensure_schema)
        schema_param = sig.parameters["schema"]
        assert schema_param.default == DB_SCHEMA


class TestDatabaseSettingsValidator:
    """Verify the schema_ validator rejects non-canonical values."""

    def test_no_env_var_uses_default(self, monkeypatch):
        monkeypatch.delenv("DATABASE_SCHEMA", raising=False)
        from src.config.settings import DatabaseSettings

        s = DatabaseSettings()
        assert s.schema_ == "neomagi"

    def test_correct_env_var_accepted(self, monkeypatch):
        monkeypatch.setenv("DATABASE_SCHEMA", "neomagi")
        from src.config.settings import DatabaseSettings

        s = DatabaseSettings()
        assert s.schema_ == "neomagi"

    def test_wrong_env_var_rejected(self, monkeypatch):
        monkeypatch.setenv("DATABASE_SCHEMA", "public")
        from src.config.settings import DatabaseSettings

        with pytest.raises(Exception) as exc_info:
            DatabaseSettings()
        assert "ADR 0017" in str(exc_info.value)
