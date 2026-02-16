from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

logger = logging.getLogger(__name__)


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
    """In-memory session storage. Will be replaced by PostgreSQL in M2."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get_or_create(self, session_id: str) -> Session:
        """Get existing session or create a new one."""
        if session_id not in self._sessions:
            logger.info("Creating new session: %s", session_id)
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
        """Append a message to a session. Creates session if needed."""
        session = self.get_or_create(session_id)
        msg = Message(
            role=role,
            content=content,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
        )
        session.messages.append(msg)
        session.updated_at = msg.timestamp
        logger.debug("Appended %s message to session %s", role, session_id)
        return msg

    def get_history(self, session_id: str) -> list[dict[str, Any]]:
        """Get message history in OpenAI chat format.

        Returns dicts with role, content, and optionally tool_calls / tool_call_id.
        """
        session = self.get_or_create(session_id)
        result: list[dict[str, Any]] = []
        for m in session.messages:
            msg_dict: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.tool_calls is not None:
                msg_dict["tool_calls"] = m.tool_calls
            if m.tool_call_id is not None:
                msg_dict["tool_call_id"] = m.tool_call_id
            result.append(msg_dict)
        return result


def resolve_session(channel_type: str, channel_id: str) -> str:
    """Resolve a session ID from channel type and ID.

    - DM messages -> "main" (shared session)
    - Group messages -> "group:{channel_id}"
    """
    if channel_type == "dm":
        return "main"
    return f"group:{channel_id}"
