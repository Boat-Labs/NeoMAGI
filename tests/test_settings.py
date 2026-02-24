"""Tests for SessionSettings.dm_scope (Phase 0, ADR 0034)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.config.settings import SessionSettings


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
