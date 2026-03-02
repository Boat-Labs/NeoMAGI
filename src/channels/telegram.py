"""Telegram DM adapter: bridges Telegram messages to NeoMAGI dispatch core.

Single-worker long-polling design. Group messages are silently ignored.
Only whitelisted user IDs (TELEGRAM_ALLOWED_USER_IDS) may interact; empty whitelist = deny all.
"""

from __future__ import annotations

import asyncio

import structlog
from aiogram import Bot, Dispatcher
from aiogram.enums import ChatAction, ChatType
from aiogram.types import Message

from src.agent.events import TextChunk
from src.agent.provider_registry import AgentLoopRegistry
from src.channels.telegram_render import format_for_telegram, friendly_error_message, split_message
from src.config.settings import GatewaySettings, TelegramSettings
from src.gateway.budget_gate import BudgetGate
from src.gateway.dispatch import dispatch_chat
from src.infra.errors import ChannelError, GatewayError
from src.session.manager import SessionManager
from src.session.scope_resolver import SessionIdentity, resolve_session_key

logger = structlog.get_logger()

# Typing indicator interval (Telegram requires refresh every ~5s)
_TYPING_INTERVAL_S = 4


def _parse_allowed_ids(raw: str) -> frozenset[int]:
    """Parse comma-separated Telegram user ID whitelist."""
    if not raw.strip():
        return frozenset()
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            ids.append(int(part))
    return frozenset(ids)


class TelegramAdapter:
    """Bridges Telegram DM messages to NeoMAGI dispatch core."""

    def __init__(
        self,
        bot_token: str,
        telegram_settings: TelegramSettings,
        registry: AgentLoopRegistry,
        session_manager: SessionManager,
        budget_gate: BudgetGate,
        gateway_settings: GatewaySettings,
    ) -> None:
        self._bot = Bot(token=bot_token)
        self._dp = Dispatcher()
        self._settings = telegram_settings
        self._registry = registry
        self._session_manager = session_manager
        self._budget_gate = budget_gate
        self._gateway_settings = gateway_settings
        self._allowed_ids = _parse_allowed_ids(telegram_settings.allowed_user_ids)
        self._bot_username: str = ""

        # Register DM handler
        self._dp.message.register(self._handle_dm)

    async def check_ready(self) -> None:
        """Verify bot token and connectivity via getMe. Raises ChannelError on failure."""
        try:
            me = await self._bot.get_me()
            self._bot_username = me.username or ""
            logger.info("telegram_bot_ready", username=self._bot_username)
        except Exception as exc:
            raise ChannelError(
                f"Telegram bot token verification failed: {exc}",
                code="TELEGRAM_AUTH_FAILED",
            ) from exc

    async def start_polling(self) -> None:
        """Start long-polling. Blocks until stopped or fatal error."""
        logger.info("telegram_polling_started", username=self._bot_username)
        await self._dp.start_polling(self._bot)

    async def stop(self) -> None:
        """Gracefully stop polling and close bot session."""
        await self._dp.stop_polling()
        await self._bot.session.close()
        logger.info("telegram_polling_stopped")

    async def _handle_dm(self, message: Message) -> None:
        """Process incoming Telegram message. Only DM text from allowed users."""
        # Ignore non-private (group) messages
        if message.chat.type != ChatType.PRIVATE:
            return

        user = message.from_user
        if user is None:
            return

        user_id = user.id

        # Auth: fail-closed — empty whitelist rejects everyone
        if user_id not in self._allowed_ids:
            logger.warning(
                "telegram_user_denied",
                user_id=user_id,
                username=user.username,
            )
            return

        # Ignore non-text messages
        if not message.text:
            return

        content = message.text
        peer_id = str(user_id)

        # Build identity via resolver
        identity = SessionIdentity(
            session_id="",  # placeholder, resolved below
            channel_type="telegram",
            peer_id=peer_id,
        )
        dm_scope = self._settings.dm_scope
        session_id = resolve_session_key(identity, dm_scope)
        identity = SessionIdentity(
            session_id=session_id,
            channel_type="telegram",
            peer_id=peer_id,
        )

        # Start typing indicator
        typing_task = asyncio.create_task(
            self._typing_loop(message.chat.id), name="tg_typing",
        )

        try:
            # Buffer all events from dispatch_chat
            chunks: list[str] = []
            async for event in dispatch_chat(
                registry=self._registry,
                session_manager=self._session_manager,
                budget_gate=self._budget_gate,
                session_id=session_id,
                content=content,
                identity=identity,
                dm_scope=dm_scope,
                session_claim_ttl_seconds=self._gateway_settings.session_claim_ttl_seconds,
            ):
                if isinstance(event, TextChunk):
                    chunks.append(event.content)

            response = "".join(chunks).strip()
            if response:
                await self._send_response(message, response)

        except GatewayError as exc:
            logger.exception(
                "telegram_dispatch_error",
                user_id=user_id,
                session_id=session_id,
                error_code=exc.code,
            )
            await message.answer(friendly_error_message(exc.code))
        except Exception:
            logger.exception(
                "telegram_dispatch_error",
                user_id=user_id,
                session_id=session_id,
            )
            await message.answer(friendly_error_message(None))
        finally:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

    async def _send_response(self, message: Message, text: str) -> None:
        """Send response with optional MarkdownV2 formatting and message splitting."""
        formatted, parse_mode = format_for_telegram(text)
        target = formatted if parse_mode else text
        parts = split_message(target, self._settings.message_max_length)
        for part in parts:
            try:
                await message.answer(part, parse_mode=parse_mode)
            except Exception:
                if parse_mode:
                    logger.debug("telegram_markdownv2_send_failed")
                    await message.answer(part, parse_mode=None)
                else:
                    raise

    async def _typing_loop(self, chat_id: int) -> None:
        """Send typing indicator every _TYPING_INTERVAL_S until cancelled."""
        while True:
            try:
                await self._bot.send_chat_action(chat_id, ChatAction.TYPING)
            except Exception:
                logger.debug("telegram_typing_failed", chat_id=chat_id)
            await asyncio.sleep(_TYPING_INTERVAL_S)
