"""Tests for P2-M3a auth boundary: Origin enforcement, Telegram no-auth, HTTP errors."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.auth.settings import AuthSettings
from src.gateway.app import _is_allowed_origin, app

# ---------------------------------------------------------------------------
# Origin enforcement
# ---------------------------------------------------------------------------


class TestIsAllowedOrigin:
    def _make_state(self, *, password_hash=None, allowed_origins=""):
        state = MagicMock()
        state.auth_settings = AuthSettings(password_hash=password_hash)
        state.settings = MagicMock()
        state.settings.gateway.allowed_origins = allowed_origins
        return state

    def _make_request(self, origin: str | None):
        req = MagicMock()
        req.headers = {"origin": origin} if origin else {}
        return req

    def test_noauth_always_allowed(self):
        state = self._make_state(password_hash=None)
        assert _is_allowed_origin(self._make_request("http://evil.com"), state) is True

    def test_auth_no_origins_configured_allowed(self):
        state = self._make_state(
            password_hash="$2b$12$test", allowed_origins="",
        )
        assert _is_allowed_origin(self._make_request("http://evil.com"), state) is True

    def test_auth_whitespace_origins_treated_as_unconfigured(self):
        state = self._make_state(
            password_hash="$2b$12$test", allowed_origins="  ",
        )
        assert _is_allowed_origin(self._make_request("http://evil.com"), state) is True

    def test_auth_allowed_origin_passes(self):
        state = self._make_state(
            password_hash="$2b$12$test",
            allowed_origins="http://localhost:5173, http://127.0.0.1:5173",
        )
        assert _is_allowed_origin(
            self._make_request("http://localhost:5173"), state,
        ) is True

    def test_auth_denied_origin_rejected(self):
        state = self._make_state(
            password_hash="$2b$12$test",
            allowed_origins="http://localhost:5173",
        )
        assert _is_allowed_origin(
            self._make_request("http://evil.com"), state,
        ) is False

    def test_auth_missing_origin_header_rejected(self):
        state = self._make_state(
            password_hash="$2b$12$test",
            allowed_origins="http://localhost:5173",
        )
        assert _is_allowed_origin(self._make_request(None), state) is False


# ---------------------------------------------------------------------------
# /auth/login Origin check + HTTP error format
# ---------------------------------------------------------------------------


class TestLoginOriginAndErrors:
    @pytest.mark.asyncio
    async def test_login_origin_denied_returns_403(self):
        """auth/login with denied Origin returns 403 with error structure."""
        mock_state = MagicMock()
        mock_state.auth_settings = AuthSettings(password_hash="$2b$12$fakehash")
        mock_state.settings = MagicMock()
        mock_state.settings.gateway.allowed_origins = "http://localhost:5173"
        mock_state.rate_limiter = MagicMock()
        mock_state.rate_limiter.is_locked = MagicMock(return_value=False)

        with patch.object(app, "state", mock_state):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test",
            ) as client:
                resp = await client.post(
                    "/auth/login",
                    json={"password": "x"},
                    headers={"origin": "http://evil.com"},
                )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "ORIGIN_DENIED"

    @pytest.mark.asyncio
    async def test_login_noauth_returns_405(self):
        """auth/login when AUTH_PASSWORD_HASH unset returns 405."""
        mock_state = MagicMock()
        mock_state.auth_settings = AuthSettings(password_hash=None)

        with patch.object(app, "state", mock_state):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test",
            ) as client:
                resp = await client.post("/auth/login", json={"password": "x"})
        assert resp.status_code == 405
        assert resp.json()["error"]["code"] == "AUTH_NOT_CONFIGURED"


# ---------------------------------------------------------------------------
# Telegram no-auth: principal_store=None
# ---------------------------------------------------------------------------


class TestTelegramNoAuth:
    def test_adapter_receives_none_store_in_noauth(self):
        """When AUTH_PASSWORD_HASH is unset, _start_telegram passes principal_store=None."""
        from src.channels.telegram import TelegramAdapter
        from src.config.settings import GatewaySettings, TelegramSettings

        adapter = TelegramAdapter(
            bot_token="123456:ABCdef",
            telegram_settings=TelegramSettings(bot_token="123456:ABCdef"),
            registry=MagicMock(),
            session_manager=MagicMock(),
            budget_gate=MagicMock(),
            gateway_settings=GatewaySettings(),
            principal_store=None,
        )
        assert adapter._principal_store is None

    @pytest.mark.asyncio
    async def test_enrich_identity_noop_without_store(self):
        """_enrich_identity_with_principal returns identity unchanged when store is None."""
        from src.channels.telegram import TelegramAdapter
        from src.config.settings import GatewaySettings, TelegramSettings
        from src.session.scope_resolver import SessionIdentity

        adapter = TelegramAdapter(
            bot_token="123456:ABCdef",
            telegram_settings=TelegramSettings(bot_token="123456:ABCdef"),
            registry=MagicMock(),
            session_manager=MagicMock(),
            budget_gate=MagicMock(),
            gateway_settings=GatewaySettings(),
            principal_store=None,
        )
        identity = SessionIdentity(session_id="tg:peer:1", channel_type="telegram", peer_id="1")
        result = await adapter._enrich_identity_with_principal(identity, "1")
        assert result is identity
        assert result.principal_id is None
