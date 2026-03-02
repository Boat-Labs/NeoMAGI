"""Tests for SessionSettings.dm_scope and TelegramSettings (Phase 0, ADR 0034)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config.settings import SessionSettings, TelegramSettings


class TestSessionSettingsDmScope:
    def test_default_dm_scope_is_main(self) -> None:
        s = SessionSettings()
        assert s.dm_scope == "main"

    def test_explicit_main_accepted(self) -> None:
        s = SessionSettings(dm_scope="main")
        assert s.dm_scope == "main"

    def test_non_main_rejected(self) -> None:
        with pytest.raises(ValidationError, match="SESSION_DM_SCOPE must be 'main'"):
            SessionSettings(dm_scope="per-peer")

    def test_empty_string_rejected(self) -> None:
        with pytest.raises(ValidationError, match="SESSION_DM_SCOPE must be 'main'"):
            SessionSettings(dm_scope="")

    def test_default_mode_unchanged(self) -> None:
        s = SessionSettings()
        assert s.default_mode == "chat_safe"


class TestTelegramSettings:
    def test_defaults(self) -> None:
        s = TelegramSettings()
        assert s.bot_token == ""
        assert s.dm_scope == "per-channel-peer"
        assert s.allowed_user_ids == ""
        assert s.message_max_length == 4096

    def test_per_channel_peer_accepted(self) -> None:
        s = TelegramSettings(dm_scope="per-channel-peer")
        assert s.dm_scope == "per-channel-peer"

    def test_per_peer_accepted(self) -> None:
        s = TelegramSettings(dm_scope="per-peer")
        assert s.dm_scope == "per-peer"

    def test_main_accepted(self) -> None:
        s = TelegramSettings(dm_scope="main")
        assert s.dm_scope == "main"

    def test_invalid_dm_scope_rejected(self) -> None:
        with pytest.raises(ValidationError, match="TELEGRAM_DM_SCOPE must be one of"):
            TelegramSettings(dm_scope="invalid")

    def test_empty_dm_scope_rejected(self) -> None:
        with pytest.raises(ValidationError, match="TELEGRAM_DM_SCOPE must be one of"):
            TelegramSettings(dm_scope="")
