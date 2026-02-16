from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class Message:
    role: Literal["user", "assistant", "system"]
    content: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Session:
    id: str
    messages: list[Message] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class SessionManager:
    """In-memory session storage for M1.1. Will be replaced by PostgreSQL in M2."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str) -> Session:
        """Get existing session or create a new one."""
        if session_id not in self._sessions:
            logger.info("Creating new session: %s", session_id)
            self._sessions[session_id] = Session(id=session_id)
        return self._sessions[session_id]

    def append_message(self, session_id: str, role: str, content: str) -> Message:
        """Append a message to a session. Creates session if needed."""
        session = self.get_or_create(session_id)
        msg = Message(role=role, content=content)
        session.messages.append(msg)
        session.updated_at = msg.timestamp
        logger.debug("Appended %s message to session %s", role, session_id)
        return msg

    def get_history(self, session_id: str) -> list[dict[str, str]]:
        """Get message history in OpenAI chat format: [{"role": ..., "content": ...}]."""
        session = self.get_or_create(session_id)
        return [{"role": m.role, "content": m.content} for m in session.messages]


def resolve_session(channel_type: str, channel_id: str) -> str:
    """Resolve a session ID from channel type and ID.

    - DM messages -> "main" (shared session)
    - Group messages -> "group:{channel_id}"
    """
    if channel_type == "dm":
        return "main"
    return f"group:{channel_id}"
