"""Tests for GeminiSettings and ProviderSettings (M6 Phase 0)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config.settings import GeminiSettings, ProviderSettings, Settings


class TestProviderSettings:
    """ProviderSettings validation tests."""

    def test_default_active_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PROVIDER_ACTIVE", raising=False)
        ps = ProviderSettings()
        assert ps.active == "openai"

    def test_active_gemini_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROVIDER_ACTIVE", "gemini")
        ps = ProviderSettings()
        assert ps.active == "gemini"

    def test_active_unknown_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROVIDER_ACTIVE", "unknown")
        with pytest.raises(ValidationError, match="PROVIDER_ACTIVE must be one of"):
            ProviderSettings()


class TestGeminiSettings:
    """GeminiSettings validation tests."""

    def test_empty_api_key_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        gs = GeminiSettings()
        assert gs.api_key == ""

    def test_api_key_loaded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
        gs = GeminiSettings()
        assert gs.api_key == "test-key-123"

    def test_default_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        gs = GeminiSettings()
        assert gs.model == "gemini-2.5-flash"

    def test_default_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GEMINI_BASE_URL", raising=False)
        gs = GeminiSettings()
        assert gs.base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"


class TestSettingsRootIncludesProviders:
    """Settings root includes gemini and provider sub-configurations."""

    def test_settings_has_gemini(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "fake")
        monkeypatch.delenv("PROVIDER_ACTIVE", raising=False)
        s = Settings()
        assert isinstance(s.gemini, GeminiSettings)

    def test_settings_has_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "fake")
        monkeypatch.delenv("PROVIDER_ACTIVE", raising=False)
        s = Settings()
        assert isinstance(s.provider, ProviderSettings)
