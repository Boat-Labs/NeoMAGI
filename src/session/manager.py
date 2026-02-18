from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.session.models import MessageRecord, SessionRecord

logger = structlog.get_logger()


@dataclass
class Message:
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


@dataclass
class Session:
    id: str
    messages: list[Message] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SessionManager:
    """Session storage with in-memory cache and optional PostgreSQL persistence."""

    def __init__(self, db_session_factory: async_sessionmaker | None = None) -> None:
        self._sessions: dict[str, Session] = {}
        self._db: async_sessionmaker | None = db_session_factory

    def get_or_create(self, session_id: str) -> Session:
        """Get existing session or create a new one (in-memory)."""
        if session_id not in self._sessions:
            logger.info("session_created", session_id=session_id)
            self._sessions[session_id] = Session(id=session_id)
        return self._sessions[session_id]

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
    ) -> Message:
        """Append a message to a session. Creates session if needed.

        Writes to memory immediately, and schedules async DB write if available.
        """
        session = self.get_or_create(session_id)
        msg = Message(
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
        )
        session.messages.append(msg)
        session.updated_at = msg.timestamp
        logger.debug("message_appended", role=role, session_id=session_id)

        # Fire-and-forget DB write
        if self._db is not None:
            seq = len(session.messages) - 1
            asyncio.create_task(self._persist_message(session_id, msg, seq))

        return msg

    def get_history(self, session_id: str) -> list[dict[str, Any]]:
        """Get message history in OpenAI chat format.

        Returns dicts with role, content, and optionally tool_calls / tool_call_id.
        """
        session = self.get_or_create(session_id)
        return _messages_to_openai_format(session.messages)

    async def load_session_from_db(self, session_id: str) -> bool:
        """Load a session from DB into memory cache. Returns True if found."""
        if self._db is None:
            return False
        if session_id in self._sessions:
            return True

        try:
            async with self._db() as db_session:
                stmt = (
                    select(SessionRecord)
                    .where(SessionRecord.id == session_id)
                )
                result = await db_session.execute(stmt)
                record = result.scalar_one_or_none()
                if record is None:
                    return False

                msg_stmt = (
                    select(MessageRecord)
                    .where(MessageRecord.session_id == session_id)
                    .order_by(MessageRecord.seq)
                )
                msg_result = await db_session.execute(msg_stmt)
                msg_records = msg_result.scalars().all()

                messages = [
                    Message(
                        role=mr.role,
                        content=mr.content,
                        timestamp=(
                            mr.created_at.replace(tzinfo=UTC)
                            if mr.created_at
                            else datetime.now(UTC)
                        ),
                        tool_calls=mr.tool_calls,
                        tool_call_id=mr.tool_call_id,
                    )
                    for mr in msg_records
                ]
                self._sessions[session_id] = Session(
                    id=session_id,
                    messages=messages,
                    created_at=(
                        record.created_at.replace(tzinfo=UTC)
                        if record.created_at
                        else datetime.now(UTC)
                    ),
                    updated_at=(
                        record.updated_at.replace(tzinfo=UTC)
                        if record.updated_at
                        else datetime.now(UTC)
                    ),
                )
                logger.info(
                    "session_loaded_from_db",
                    session_id=session_id,
                    message_count=len(messages),
                )
                return True
        except Exception:
            logger.exception("session_load_failed", session_id=session_id)
            return False

    async def get_history_for_display(self, session_id: str) -> list[dict[str, Any]]:
        """Get filtered history for chat UI. Only user + assistant with content."""
        # [Decision 0019] chat.history is a UI history API, not an internal context export API.
        await self.load_session_from_db(session_id)
        session = self._sessions.get(session_id)
        if session is None:
            return []
        return _messages_to_history_format(session.messages)

    async def get_history_from_db(self, session_id: str) -> list[dict[str, Any]]:
        """Get history from DB (for chat.history RPC). Falls back to memory."""
        # Try loading from DB first
        await self.load_session_from_db(session_id)
        # Return from memory (which may now include DB-loaded data)
        session = self._sessions.get(session_id)
        if session is None:
            return []
        return _messages_to_openai_format(session.messages)

    async def _persist_message(self, session_id: str, msg: Message, seq: int) -> None:
        """Write a single message to DB. Fire-and-forget, errors logged."""
        if self._db is None:
            return
        try:
            async with self._db() as db_session:
                # Ensure session record exists
                existing = await db_session.execute(
                    select(SessionRecord).where(SessionRecord.id == session_id)
                )
                if existing.scalar_one_or_none() is None:
                    db_session.add(SessionRecord(id=session_id))

                db_session.add(MessageRecord(
                    session_id=session_id,
                    seq=seq,
                    role=msg.role,
                    content=msg.content,
                    tool_calls=msg.tool_calls,
                    tool_call_id=msg.tool_call_id,
                ))
                await db_session.commit()
        except Exception:
            logger.exception("message_persist_failed", session_id=session_id, seq=seq)


def _messages_to_openai_format(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert Message list to OpenAI chat format dicts."""
    result: list[dict[str, Any]] = []
    for m in messages:
        msg_dict: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls is not None:
            msg_dict["tool_calls"] = m.tool_calls
        if m.tool_call_id is not None:
            msg_dict["tool_call_id"] = m.tool_call_id
        result.append(msg_dict)
    return result


def _messages_to_history_format(messages: list[Message]) -> list[dict[str, Any]]:
    """Convert Message list to display-friendly format for chat history.

    Only includes user + assistant messages with non-empty content.
    Strips tool_calls/tool_call_id to avoid leaking internal state.
    """
    # [Decision 0019] Keep display schema minimal and stable: user/assistant + content (+ timestamp).
    result: list[dict[str, Any]] = []
    for m in messages:
        if m.role not in ("user", "assistant"):
            continue
        if not m.content:
            continue
        result.append({
            "role": m.role,
            "content": m.content,
            "timestamp": m.timestamp.isoformat(),
        })
    return result


def resolve_session(channel_type: str, channel_id: str) -> str:
    """Resolve a session ID from channel type and ID.

    - DM messages -> "main" (shared session)
    - Group messages -> "group:{channel_id}"
    """
    if channel_type == "dm":
        return "main"
    return f"group:{channel_id}"
