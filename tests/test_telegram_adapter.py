"""Tests for TelegramAdapter: identity mapping, auth gating, dispatch, lifecycle."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agent.events import TextChunk, ToolCallInfo
from src.channels.telegram import TelegramAdapter, _parse_allowed_ids
from src.config.settings import GatewaySettings, TelegramSettings
from src.infra.errors import ChannelError
from src.session.scope_resolver import resolve_session_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(
    allowed_ids: str = "111,222",
    dm_scope: str = "per-channel-peer",
    bot_token: str = "123456789:ABCdefGHIjklmNOPqrs",
) -> TelegramSettings:
    return TelegramSettings(
        bot_token=bot_token,
        dm_scope=dm_scope,
        allowed_user_ids=allowed_ids,
        message_max_length=4096,
    )


def _make_adapter(
    telegram_settings: TelegramSettings | None = None,
) -> TelegramAdapter:
    settings = telegram_settings or _make_settings()
    return TelegramAdapter(
        bot_token=settings.bot_token,
        telegram_settings=settings,
        registry=MagicMock(),
        session_manager=MagicMock(),
        budget_gate=MagicMock(),
        gateway_settings=GatewaySettings(),
    )


def _make_message(
    user_id: int = 111,
    username: str = "testuser",
    text: str = "hello",
    chat_type: str = "private",
) -> MagicMock:
    """Create a mock aiogram Message."""
    msg = AsyncMock()
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.from_user.username = username
    msg.text = text
    msg.chat = MagicMock()
    msg.chat.id = user_id
    msg.chat.type = chat_type
    msg.answer = AsyncMock()
    return msg


# ---------------------------------------------------------------------------
# _parse_allowed_ids
# ---------------------------------------------------------------------------


class TestParseAllowedIds:
    def test_empty_string(self):
        assert _parse_allowed_ids("") == frozenset()

    def test_whitespace_only(self):
        assert _parse_allowed_ids("   ") == frozenset()

    def test_single_id(self):
        assert _parse_allowed_ids("123") == frozenset({123})

    def test_multiple_ids(self):
        assert _parse_allowed_ids("111, 222 ,333") == frozenset({111, 222, 333})


# ---------------------------------------------------------------------------
# Identity mapping via resolver
# ---------------------------------------------------------------------------


class TestIdentityMapping:
    def test_session_key_via_resolver(self):
        """Session key must come from resolve_session_key, not manual string build."""
        from src.session.scope_resolver import SessionIdentity

        identity = SessionIdentity(
            session_id="",
            channel_type="telegram",
            peer_id="12345",
        )
        key = resolve_session_key(identity, "per-channel-peer")
        assert key == "telegram:peer:12345"

    def test_per_peer_scope(self):
        from src.session.scope_resolver import SessionIdentity

        identity = SessionIdentity(
            session_id="",
            channel_type="telegram",
            peer_id="99",
        )
        key = resolve_session_key(identity, "per-peer")
        assert key == "peer:99"

    def test_main_scope(self):
        from src.session.scope_resolver import SessionIdentity

        identity = SessionIdentity(
            session_id="",
            channel_type="telegram",
            peer_id="99",
        )
        key = resolve_session_key(identity, "main")
        assert key == "main"


# ---------------------------------------------------------------------------
# Auth gating
# ---------------------------------------------------------------------------


class TestAuthGating:
    @pytest.mark.asyncio
    async def test_allowed_user_passes(self):
        """Whitelisted user triggers dispatch."""
        adapter = _make_adapter()
        msg = _make_message(user_id=111, text="hi")

        async def _fake_dispatch(**kwargs):
            yield TextChunk(content="response")

        with patch("src.channels.telegram.dispatch_chat", side_effect=_fake_dispatch):
            await adapter._handle_dm(msg)

        msg.answer.assert_awaited_once_with("response", parse_mode=None)

    @pytest.mark.asyncio
    async def test_denied_user_ignored(self):
        """Non-whitelisted user message is silently ignored."""
        adapter = _make_adapter()
        msg = _make_message(user_id=999, text="hi")

        await adapter._handle_dm(msg)

        msg.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_whitelist_denies_all(self):
        """Empty TELEGRAM_ALLOWED_USER_IDS rejects everyone (fail-closed)."""
        settings = _make_settings(allowed_ids="")
        adapter = _make_adapter(telegram_settings=settings)
        msg = _make_message(user_id=111, text="hi")

        await adapter._handle_dm(msg)

        msg.answer.assert_not_awaited()


# ---------------------------------------------------------------------------
# DM dispatch
# ---------------------------------------------------------------------------


class TestDmDispatch:
    @pytest.mark.asyncio
    async def test_dm_triggers_dispatch(self):
        """DM from allowed user calls dispatch_chat with correct params."""
        adapter = _make_adapter()
        msg = _make_message(user_id=111, text="test message")

        captured_kwargs = {}

        async def _capture_dispatch(**kwargs):
            captured_kwargs.update(kwargs)
            yield TextChunk(content="ok")

        with patch("src.channels.telegram.dispatch_chat", side_effect=_capture_dispatch):
            await adapter._handle_dm(msg)

        assert captured_kwargs["session_id"] == "telegram:peer:111"
        assert captured_kwargs["content"] == "test message"
        assert captured_kwargs["identity"].channel_type == "telegram"
        assert captured_kwargs["identity"].peer_id == "111"
        assert captured_kwargs["dm_scope"] == "per-channel-peer"

    @pytest.mark.asyncio
    async def test_buffers_multiple_chunks(self):
        """Multiple TextChunk events are buffered and sent as single message."""
        adapter = _make_adapter()
        msg = _make_message(user_id=111, text="hi")

        async def _multi_chunk(**kwargs):
            yield TextChunk(content="Hello ")
            yield TextChunk(content="World")

        with patch("src.channels.telegram.dispatch_chat", side_effect=_multi_chunk):
            await adapter._handle_dm(msg)

        msg.answer.assert_awaited_once_with("Hello World", parse_mode=None)

    @pytest.mark.asyncio
    async def test_non_text_events_ignored(self):
        """Non-TextChunk events (ToolCallInfo etc.) are not included in response."""
        adapter = _make_adapter()
        msg = _make_message(user_id=111, text="hi")

        async def _mixed_events(**kwargs):
            yield TextChunk(content="text")
            yield ToolCallInfo(tool_name="test", arguments={}, call_id="c1")

        with patch("src.channels.telegram.dispatch_chat", side_effect=_mixed_events):
            await adapter._handle_dm(msg)

        msg.answer.assert_awaited_once_with("text", parse_mode=None)

    @pytest.mark.asyncio
    async def test_empty_response_not_sent(self):
        """If dispatch produces no text, no message is sent back."""
        adapter = _make_adapter()
        msg = _make_message(user_id=111, text="hi")

        async def _no_text(**kwargs):
            yield ToolCallInfo(tool_name="test", arguments={}, call_id="c1")

        with patch("src.channels.telegram.dispatch_chat", side_effect=_no_text):
            await adapter._handle_dm(msg)

        msg.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_long_response_split(self):
        """Responses exceeding message_max_length are split into multiple messages."""
        settings = _make_settings()
        settings.message_max_length = 20
        adapter = _make_adapter(telegram_settings=settings)
        msg = _make_message(user_id=111, text="hi")

        async def _long_response(**kwargs):
            yield TextChunk(content="A" * 50)

        with patch("src.channels.telegram.dispatch_chat", side_effect=_long_response):
            await adapter._handle_dm(msg)

        assert msg.answer.await_count == 3
        for call in msg.answer.call_args_list:
            assert len(call[0][0]) <= 20


# ---------------------------------------------------------------------------
# Group messages ignored
# ---------------------------------------------------------------------------


class TestGroupMessages:
    @pytest.mark.asyncio
    async def test_group_message_ignored(self):
        """Non-private (group) messages are silently dropped."""
        adapter = _make_adapter()
        msg = _make_message(user_id=111, text="hi", chat_type="group")

        await adapter._handle_dm(msg)

        msg.answer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_supergroup_message_ignored(self):
        adapter = _make_adapter()
        msg = _make_message(user_id=111, text="hi", chat_type="supergroup")

        await adapter._handle_dm(msg)

        msg.answer.assert_not_awaited()


# ---------------------------------------------------------------------------
# Readiness check
# ---------------------------------------------------------------------------


class TestReadinessCheck:
    @pytest.mark.asyncio
    async def test_check_ready_success(self):
        """Successful getMe stores bot username."""
        adapter = _make_adapter()
        mock_me = MagicMock()
        mock_me.username = "test_bot"

        with patch.object(adapter._bot, "get_me", return_value=mock_me):
            await adapter.check_ready()

        assert adapter._bot_username == "test_bot"

    @pytest.mark.asyncio
    async def test_check_ready_failure_raises(self):
        """Failed getMe raises ChannelError."""
        adapter = _make_adapter()

        with patch.object(
            adapter._bot, "get_me", side_effect=Exception("network error"),
        ):
            with pytest.raises(ChannelError, match="Telegram bot token verification failed"):
                await adapter.check_ready()


# ---------------------------------------------------------------------------
# Dispatch error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_dispatch_error_sends_fallback(self):
        """Dispatch exception sends error message to user."""
        adapter = _make_adapter()
        msg = _make_message(user_id=111, text="hi")

        async def _fail(**kwargs):
            raise RuntimeError("boom")
            yield  # noqa: RET503 — make it a generator

        with patch("src.channels.telegram.dispatch_chat", side_effect=_fail):
            await adapter._handle_dm(msg)

        msg.answer.assert_awaited_once()
        sent = msg.answer.call_args[0][0]
        assert "处理消息时遇到了问题" in sent


# ---------------------------------------------------------------------------
# No-text message ignored
# ---------------------------------------------------------------------------


class TestNoTextMessage:
    @pytest.mark.asyncio
    async def test_no_text_ignored(self):
        """Message without text (e.g. sticker) is ignored."""
        adapter = _make_adapter()
        msg = _make_message(user_id=111)
        msg.text = None

        await adapter._handle_dm(msg)

        msg.answer.assert_not_awaited()
