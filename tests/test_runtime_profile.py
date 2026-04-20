"""Tests for P3-M1 Slice A: runtime profile + daily mode.

Covers:
- Settings: ClaudeSettings, RuntimeProfileSettings, OpenAI key optional
- Preflight: C2 claude support, C9 daily skip, C11 lightweight reconcile
- Provider: Claude-only config validation
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from src.config.settings import (
    ClaudeSettings,
    OpenAISettings,
    ProviderSettings,
    RuntimeProfileSettings,
    Settings,
)
from src.infra.health import CheckStatus
from src.infra.preflight import (
    _check_active_provider,
    _check_schema_tables,
    _check_soul_reconcile_lightweight,
    run_preflight,
)

# ── Helpers ──


def _make_settings(**overrides: object) -> MagicMock:
    """Build a mock Settings with sane defaults including claude + runtime."""
    ws = overrides.pop("workspace_dir", Path("/tmp/test_ws"))
    settings = MagicMock()
    settings.workspace_dir = ws
    settings.memory.workspace_path = overrides.pop("memory_workspace_path", ws)
    settings.provider.active = overrides.pop("provider_active", "openai")
    settings.openai.api_key = overrides.pop("openai_api_key", "sk-test")
    settings.gemini.api_key = overrides.pop("gemini_api_key", "")
    settings.claude.api_key = overrides.pop("claude_api_key", "")
    settings.telegram.bot_token = overrides.pop("telegram_bot_token", "")
    settings.runtime.profile = overrides.pop("runtime_profile", "daily")
    settings.database.schema_ = "neomagi"
    settings.auth.password_hash = None
    return settings


def _async_ctx(obj: object) -> MagicMock:
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=obj)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _mock_engine_ok() -> AsyncMock:
    engine = AsyncMock()
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value=MagicMock())
    engine.connect = MagicMock(return_value=_async_ctx(conn))
    return engine


# ── RuntimeProfileSettings tests ──


class TestRuntimeProfileSettings:
    def test_default_is_daily(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RUNTIME_PROFILE", raising=False)
        rps = RuntimeProfileSettings()
        assert rps.profile == "daily"

    def test_growth_lab_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RUNTIME_PROFILE", "growth_lab")
        rps = RuntimeProfileSettings()
        assert rps.profile == "growth_lab"

    def test_invalid_profile_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RUNTIME_PROFILE", "experiment")
        with pytest.raises(ValidationError, match="RUNTIME_PROFILE must be one of"):
            RuntimeProfileSettings()


# ── ClaudeSettings tests ──


class TestClaudeSettings:
    def test_empty_api_key_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLAUDE_API_KEY", raising=False)
        cs = ClaudeSettings()
        assert cs.api_key == ""

    def test_api_key_loaded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_API_KEY", "sk-ant-test")
        cs = ClaudeSettings()
        assert cs.api_key == "sk-ant-test"

    def test_default_model(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLAUDE_MODEL", raising=False)
        cs = ClaudeSettings()
        assert "claude" in cs.model


# ── OpenAI key now optional ──


class TestOpenAIKeyOptional:
    def test_empty_api_key_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        oas = OpenAISettings()
        assert oas.api_key == ""


# ── ProviderSettings claude support ──


class TestProviderSettingsClaude:
    def test_active_claude_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROVIDER_ACTIVE", "claude")
        ps = ProviderSettings()
        assert ps.active == "claude"


# ── Settings root includes claude + runtime ──


class TestSettingsRootNewFields:
    def test_settings_has_claude(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("PROVIDER_ACTIVE", raising=False)
        s = Settings()
        assert isinstance(s.claude, ClaudeSettings)

    def test_settings_has_runtime(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("RUNTIME_PROFILE", raising=False)
        s = Settings()
        assert isinstance(s.runtime, RuntimeProfileSettings)
        assert s.runtime.profile == "daily"


# ── Preflight C2: active provider recognizes claude ──


class TestPreflightC2Claude:
    def test_claude_active_with_key(self) -> None:
        settings = _make_settings(provider_active="claude", claude_api_key="sk-ant-test")
        result = _check_active_provider(settings)
        assert result.status == CheckStatus.OK

    def test_claude_active_no_key(self) -> None:
        settings = _make_settings(provider_active="claude", claude_api_key="")
        result = _check_active_provider(settings)
        assert result.status == CheckStatus.FAIL

    def test_claude_only_deploy(self) -> None:
        """Claude-only: openai key empty, claude key set, provider_active=claude."""
        settings = _make_settings(
            provider_active="claude", openai_api_key="", claude_api_key="sk-ant-test",
        )
        result = _check_active_provider(settings)
        assert result.status == CheckStatus.OK


# ── Preflight C6: daily profile uses relaxed required tables ──


class TestPreflightC6DailyProfile:
    @pytest.mark.asyncio
    async def test_daily_profile_soul_versions_optional(self) -> None:
        """daily profile: missing soul_versions table → still OK."""
        engine = AsyncMock()
        conn = AsyncMock()
        # Return all required tables EXCEPT soul_versions
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("sessions",), ("messages",), ("memory_entries",), ("memory_source_ledger",),
        ]
        conn.execute = AsyncMock(return_value=mock_result)
        engine.connect = MagicMock(return_value=_async_ctx(conn))

        result = await _check_schema_tables(engine, profile="daily")
        assert result.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_growth_lab_profile_requires_soul_versions(self) -> None:
        """growth_lab profile: missing soul_versions → FAIL."""
        engine = AsyncMock()
        conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [
            ("sessions",), ("messages",), ("memory_entries",), ("memory_source_ledger",),
        ]
        conn.execute = AsyncMock(return_value=mock_result)
        engine.connect = MagicMock(return_value=_async_ctx(conn))

        result = await _check_schema_tables(engine, profile="growth_lab")
        assert result.status == CheckStatus.FAIL
        assert "soul_versions" in result.evidence


# ── Preflight C11 lightweight reconcile ──


class TestPreflightC11Lightweight:
    @pytest.mark.asyncio
    async def test_no_active_version_warns(self, tmp_path: Path) -> None:
        """No active version in DB → WARN, not FAIL."""
        engine = AsyncMock()
        conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        conn.execute = AsyncMock(return_value=mock_result)
        engine.connect = MagicMock(return_value=_async_ctx(conn))

        settings = _make_settings(workspace_dir=tmp_path)
        result = await _check_soul_reconcile_lightweight(settings, engine)
        assert result.status == CheckStatus.WARN

    @pytest.mark.asyncio
    async def test_syncs_db_to_file(self, tmp_path: Path) -> None:
        """DB active version content synced to SOUL.md file."""
        engine = AsyncMock()
        conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = ("# Soul\nUpdated content",)
        conn.execute = AsyncMock(return_value=mock_result)
        engine.connect = MagicMock(return_value=_async_ctx(conn))

        soul_path = tmp_path / "SOUL.md"
        soul_path.write_text("# Soul\nOld content", encoding="utf-8")

        settings = _make_settings(workspace_dir=tmp_path)
        result = await _check_soul_reconcile_lightweight(settings, engine)
        assert result.status == CheckStatus.OK
        assert soul_path.read_text(encoding="utf-8") == "# Soul\nUpdated content"

    @pytest.mark.asyncio
    async def test_already_consistent(self, tmp_path: Path) -> None:
        """DB and file already match → OK, no write."""
        content = "# Soul\nSame content"
        engine = AsyncMock()
        conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = (content,)
        conn.execute = AsyncMock(return_value=mock_result)
        engine.connect = MagicMock(return_value=_async_ctx(conn))

        soul_path = tmp_path / "SOUL.md"
        soul_path.write_text(content, encoding="utf-8")

        settings = _make_settings(workspace_dir=tmp_path)
        result = await _check_soul_reconcile_lightweight(settings, engine)
        assert result.status == CheckStatus.OK
        assert "already consistent" in result.evidence

    @pytest.mark.asyncio
    async def test_table_missing_warns(self) -> None:
        """soul_versions table missing → WARN (exception caught)."""
        engine = AsyncMock()
        conn = AsyncMock()
        conn.execute = AsyncMock(side_effect=Exception("relation does not exist"))
        engine.connect = MagicMock(return_value=_async_ctx(conn))

        settings = _make_settings(workspace_dir=Path("/tmp/test_ws"))
        result = await _check_soul_reconcile_lightweight(settings, engine)
        assert result.status == CheckStatus.WARN


# ── run_preflight profile-aware ──


class TestRunPreflightDaily:
    @pytest.mark.asyncio
    async def test_daily_skips_c9(self) -> None:
        """daily profile: soul_versions_readable check not in report."""
        settings = _make_settings(runtime_profile="daily")

        engine = AsyncMock()
        conn = AsyncMock()
        mock_result = MagicMock()
        # Return all daily-required tables
        mock_result.fetchall.return_value = [
            ("sessions",), ("messages",), ("memory_entries",),
            ("memory_source_ledger",), ("budget_state",), ("budget_reservations",),
        ]
        mock_result.fetchone.return_value = (1,)  # trigger check
        mock_result.scalar.return_value = 0
        conn.execute = AsyncMock(return_value=mock_result)
        engine.connect = MagicMock(return_value=_async_ctx(conn))

        report = await run_preflight(settings, engine, profile="daily")
        check_names = [c.name for c in report.checks]
        assert "soul_versions_readable" not in check_names
        # C11 still runs (lightweight variant)
        assert "soul_reconcile" in check_names
