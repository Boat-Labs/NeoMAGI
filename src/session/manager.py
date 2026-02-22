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
from src.tools.base import ToolMode

logger = structlog.get_logger()


@dataclass
class Message:
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    seq: int | None = None  # Populated when loaded from DB or after persist


@dataclass(frozen=True)
class MessageWithSeq:
    """Message with guaranteed seq for compaction operations (ADR 0031)."""

    seq: int
    role: str
    content: str | None
    tool_calls: list[dict[str, Any]] | None
    tool_call_id: str | None


@dataclass(frozen=True)
class CompactionState:
    """Compaction state loaded from session record."""

    compacted_context: str | None
    last_compaction_seq: int | None
    compaction_metadata: dict | None


@dataclass
class Session:
    id: str
    messages: list[Message] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SessionManager:
    """Session storage with in-memory cache and PostgreSQL persistence."""

    def __init__(
        self,
        db_session_factory: async_sessionmaker,
        default_mode: ToolMode = ToolMode.chat_safe,
    ) -> None:
        self._sessions: dict[str, Session] = {}
        self._db: async_sessionmaker = db_session_factory
        self._default_mode = default_mode

    def get_or_create(self, session_id: str) -> Session:
        """Get existing session or create a new one (in-memory)."""
        if session_id not in self._sessions:
            logger.info("session_created", session_id=session_id)
            self._sessions[session_id] = Session(id=session_id)
        return self._sessions[session_id]

    async def get_mode(self, session_id: str) -> ToolMode:
        """Get the effective ToolMode for a session.

        Reads from DB. On any error or invalid value, fail-closed to chat_safe.
        M1.5 guardrail: even valid 'coding' values are downgraded to chat_safe.
        """
        try:
            async with self._db() as db_session:
                stmt = select(SessionRecord.mode).where(SessionRecord.id == session_id)
                result = await db_session.execute(stmt)
                mode_str = result.scalar_one_or_none()

            if mode_str is None:
                return self._default_mode

            try:
                mode = ToolMode(mode_str)
            except ValueError:
                logger.warning(
                    "session_mode_invalid",
                    session_id=session_id,
                    mode=mode_str,
                    msg="Invalid mode value; falling back to chat_safe",
                )
                return ToolMode.chat_safe

            # M1.5 guardrail: only chat_safe is allowed
            if mode != ToolMode.chat_safe:
                logger.warning(
                    "session_mode_downgraded",
                    session_id=session_id,
                    requested_mode=mode.value,
                    effective_mode="chat_safe",
                    msg="M1.5 guardrail: non-chat_safe modes downgraded",
                )
                return ToolMode.chat_safe

            return mode
        except Exception:
            logger.exception(
                "session_mode_read_failed",
                session_id=session_id,
                msg="DB error; falling back to chat_safe",
            )
            return ToolMode.chat_safe

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
        lock_token: str | None = None,
    ) -> Message:
        """Append a message to a session: persist to DB first, then write memory.

        [Decision 0021] Persist is synchronous — failure propagates to caller,
        no silent drop. Memory is only updated after DB confirms success,
        preventing ghost messages in local cache on failure.

        When lock_token is provided, _persist_message atomically verifies the
        token matches the current session lock holder. Raises SessionFencingError
        on mismatch (stale worker after TTL takeover).
        """
        session = self.get_or_create(session_id)
        msg = Message(
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
        )

        # [Decision 0021] Persist first — no memory pollution on failure
        seq = await self._persist_message(session_id, msg, lock_token=lock_token)
        msg.seq = seq

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

    def get_history_with_seq(self, session_id: str) -> list[MessageWithSeq]:
        """Return all messages with seq from in-memory cache.

        Messages without seq (not yet persisted) are excluded.
        """
        session = self.get_or_create(session_id)
        return [
            MessageWithSeq(
                seq=m.seq,
                role=m.role,
                content=m.content,
                tool_calls=m.tool_calls,
                tool_call_id=m.tool_call_id,
            )
            for m in session.messages
            if m.seq is not None
        ]

    def get_effective_history(
        self, session_id: str, last_compaction_seq: int | None
    ) -> list[MessageWithSeq]:
        """Unique rebuild entry point (ADR 0031).

        Returns messages WHERE seq > last_compaction_seq (or all if None).
        In-memory cache preferred.
        """
        all_msgs = self.get_history_with_seq(session_id)
        if last_compaction_seq is None:
            return all_msgs
        return [m for m in all_msgs if m.seq > last_compaction_seq]

    async def get_compaction_state(self, session_id: str) -> CompactionState | None:
        """Load compacted_context + last_compaction_seq + compaction_metadata.

        Returns None if session has no compaction history.
        """
        async with self._db() as db_session:
            stmt = select(
                SessionRecord.compacted_context,
                SessionRecord.last_compaction_seq,
                SessionRecord.compaction_metadata,
            ).where(SessionRecord.id == session_id)
            result = await db_session.execute(stmt)
            row = result.one_or_none()

        if row is None:
            return None
        if row[0] is None and row[1] is None and row[2] is None:
            return None

        return CompactionState(
            compacted_context=row[0],
            last_compaction_seq=row[1],
            compaction_metadata=row[2],
        )

    async def store_compaction_result(
        self,
        session_id: str,
        result: Any,
        *,
        lock_token: str,
    ) -> None:
        """Persist compaction state (ADR 0031 + ADR 0021 fencing).

        MUST NOT be called when result.status == "noop" (caller responsibility).

        Atomic UPDATE with lock_token fencing and monotonic seq protection.
        """
        from src.infra.errors import SessionFencingError

        async with self._db() as db_session:
            stmt = (
                update(SessionRecord)
                .where(
                    SessionRecord.id == session_id,
                    SessionRecord.lock_token == lock_token,
                    or_(
                        SessionRecord.last_compaction_seq.is_(None),
                        SessionRecord.last_compaction_seq < result.new_compaction_seq,
                    ),
                )
                .values(
                    compacted_context=result.compacted_context,
                    compaction_metadata=result.compaction_metadata,
                    last_compaction_seq=result.new_compaction_seq,
                    memory_flush_candidates=[
                        c if isinstance(c, dict) else c.__dict__
                        for c in (result.memory_flush_candidates or [])
                    ],
                )
                .returning(SessionRecord.id)
            )
            exec_result = await db_session.execute(stmt)
            updated = exec_result.scalar_one_or_none()

            if updated is None:
                raise SessionFencingError(
                    f"Compaction store failed for session {session_id}: "
                    "lock_token mismatch or seq not monotonic"
                )

            await db_session.commit()

        logger.info(
            "compaction_stored",
            session_id=session_id,
            new_compaction_seq=result.new_compaction_seq,
            status=result.status,
        )

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
                        seq=mr.seq,
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

    async def _persist_message(
        self, session_id: str, msg: Message, *, lock_token: str | None = None
    ) -> int:
        """Persist a single message to DB with atomic seq allocation and fencing.

        [Decision 0021] Raises on failure — no silent drop.
        SQLAlchemy async context manager auto-rollbacks on exception.

        When lock_token is provided, the ON CONFLICT WHERE clause atomically
        verifies the token matches the current session holder. Single statement,
        no race window between lock check and seq allocation.

        Returns the allocated seq number.
        """
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from src.infra.errors import SessionFencingError

        async with self._db() as db_session:
            # Atomic: upsert session + allocate seq (row lock serializes per-session)
            update_set = {"next_seq": SessionRecord.next_seq + 1}
            stmt = pg_insert(SessionRecord).values(id=session_id, next_seq=1)

            if lock_token is not None:
                # Fencing: only update if lock_token matches or is NULL.
                # WHERE false → no update → RETURNING empty → fencing failed.
                stmt = stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_=update_set,
                    where=or_(
                        SessionRecord.lock_token == lock_token,
                        SessionRecord.lock_token.is_(None),
                    ),
                )
            else:
                stmt = stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_=update_set,
                )

            stmt = stmt.returning(SessionRecord.next_seq - 1)
            result = await db_session.execute(stmt)
            seq = result.scalar_one_or_none()

            if seq is None:
                raise SessionFencingError(
                    f"Lock token mismatch for session {session_id}: "
                    "another worker has taken over"
                )

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
            return seq


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
