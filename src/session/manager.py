from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

import structlog
from sqlalchemy import or_, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.sql import func

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
    """Session storage with in-memory cache and PostgreSQL persistence."""

    def __init__(self, db_session_factory: async_sessionmaker) -> None:
        self._sessions: dict[str, Session] = {}
        self._db: async_sessionmaker = db_session_factory

    def get_or_create(self, session_id: str) -> Session:
        """Get existing session or create a new one (in-memory)."""
        if session_id not in self._sessions:
            logger.info("session_created", session_id=session_id)
            self._sessions[session_id] = Session(id=session_id)
        return self._sessions[session_id]

    async def try_claim_session(
        self, session_id: str, ttl_seconds: int = 300
    ) -> str | None:
        """Try to claim a session for exclusive processing.

        [Decision 0021] Session-level serialization: prevents concurrent
        multi-worker processing of the same session.

        Uses lock_token (UUID) as owner identifier. Only the holder can release.
        TTL auto-releases stale claims (crashed worker recovery).

        Returns lock_token (str) if claimed, None if session is busy.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        lock_token = str(uuid.uuid4())

        async with self._db() as db_session:
            stmt = (
                pg_insert(SessionRecord)
                .values(
                    id=session_id,
                    lock_token=lock_token,
                    processing_since=func.now(),
                    next_seq=0,
                )
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        "lock_token": lock_token,
                        "processing_since": func.now(),
                    },
                    where=or_(
                        SessionRecord.processing_since.is_(None),
                        SessionRecord.processing_since
                        < func.now() - text(f"interval '{ttl_seconds} seconds'"),
                    ),
                )
                .returning(SessionRecord.id)
            )
            result = await db_session.execute(stmt)
            claimed = result.scalar_one_or_none() is not None
            await db_session.commit()
            return lock_token if claimed else None

    async def release_session(self, session_id: str, lock_token: str) -> None:
        """Release session processing claim. Only succeeds if lock_token matches.

        If another worker has already taken over (token mismatch after TTL
        expiry), this is a no-op — prevents cascading release where Worker A
        clears Worker B's lock.
        """
        async with self._db() as db_session:
            await db_session.execute(
                update(SessionRecord)
                .where(
                    SessionRecord.id == session_id,
                    SessionRecord.lock_token == lock_token,
                )
                .values(processing_since=None, lock_token=None)
            )
            await db_session.commit()

    async def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
    ) -> Message:
        """Append a message to a session: persist to DB first, then write memory.

        [Decision 0021] Persist is synchronous — failure propagates to caller,
        no silent drop. Memory is only updated after DB confirms success,
        preventing ghost messages in local cache on failure.
        """
        session = self.get_or_create(session_id)
        msg = Message(
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
        )

        # [Decision 0021] Persist first — no memory pollution on failure
        await self._persist_message(session_id, msg)

        # Only reach here if persist succeeded
        session.messages.append(msg)
        session.updated_at = msg.timestamp
        logger.debug("message_appended", role=role, session_id=session_id)
        return msg

    def get_history(self, session_id: str) -> list[dict[str, Any]]:
        """Get message history in OpenAI chat format.

        Returns dicts with role, content, and optionally tool_calls / tool_call_id.
        """
        session = self.get_or_create(session_id)
        return _messages_to_openai_format(session.messages)

    async def load_session_from_db(self, session_id: str, *, force: bool = False) -> bool:
        """Load a session from DB into memory cache. Returns True if found.

        When force=True, unconditionally reload from DB even if session exists
        in local cache. Required for cross-worker handoff to ensure prompt is
        built from latest DB state, not stale local cache.

        [Decision 0021] force=True changes exception semantics: DB errors
        propagate instead of returning False. Prevents silent degradation to
        empty/stale context. "Session not found" (record is None) still
        returns False — a new session with no history is legitimate.
        """
        if session_id in self._sessions and not force:
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
            if force:
                raise  # [Decision 0021] force reload failure must not silently degrade
            logger.exception("session_load_failed", session_id=session_id)
            return False

    async def get_history_for_display(self, session_id: str) -> list[dict[str, Any]]:
        """Get filtered history for chat UI. Only user + assistant with content."""
        # [Decision 0019] chat.history is a UI history API, not an internal context export API.
        # Always force-reload from DB to avoid returning stale cache when
        # another worker wrote new messages since our last load.
        await self.load_session_from_db(session_id, force=True)
        session = self._sessions.get(session_id)
        if session is None:
            return []
        return _messages_to_history_format(session.messages)

    async def _persist_message(self, session_id: str, msg: Message) -> None:
        """Persist a single message to DB with atomic seq allocation.

        [Decision 0021] Raises on failure — no silent drop.
        SQLAlchemy async context manager auto-rollbacks on exception.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        async with self._db() as db_session:
            # Atomic: upsert session + allocate seq (row lock serializes per-session)
            stmt = (
                pg_insert(SessionRecord)
                .values(id=session_id, next_seq=1)
                .on_conflict_do_update(
                    index_elements=["id"],
                    set_={"next_seq": SessionRecord.next_seq + 1},
                )
                .returning(
                    # New session: next_seq 0→1, returns 1-1=0
                    # Existing session: next_seq N→N+1, returns N+1-1=N
                    SessionRecord.next_seq - 1
                )
            )
            result = await db_session.execute(stmt)
            seq = result.scalar_one()

            db_session.add(
                MessageRecord(
                    session_id=session_id,
                    seq=seq,
                    role=msg.role,
                    content=msg.content,
                    tool_calls=msg.tool_calls,
                    tool_call_id=msg.tool_call_id,
                )
            )
            await db_session.commit()


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
    # [Decision 0019] Minimal display schema: user/assistant + content + timestamp.
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
