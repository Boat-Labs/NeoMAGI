"""SQLAlchemy 2.0 async models for session persistence."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.constants import DB_SCHEMA


class Base(DeclarativeBase):
    pass


class SessionRecord(Base):
    __tablename__ = "sessions"
    __table_args__ = {"schema": DB_SCHEMA}

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False, default="chat_safe")
    next_seq: Mapped[int] = mapped_column(Integer, default=0)
    lock_token: Mapped[str | None] = mapped_column(String(36), nullable=True, default=None)
    processing_since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Compaction fields (M2)
    compacted_context: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    compaction_metadata: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=None)
    last_compaction_seq: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    memory_flush_candidates: Mapped[list | None] = mapped_column(JSONB, nullable=True, default=None)
    # P2-M3a: session ownership
    principal_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey(f"{DB_SCHEMA}.principals.id", ondelete="RESTRICT"),
        nullable=True,
        default=None,
    )

    messages: Mapped[list[MessageRecord]] = relationship(
        back_populates="session", order_by="MessageRecord.seq", cascade="all, delete-orphan"
    )


class MessageRecord(Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_messages_session_seq"),
        {"schema": DB_SCHEMA},
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(128), ForeignKey(f"{DB_SCHEMA}.sessions.id"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    tool_calls: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    session: Mapped[SessionRecord] = relationship(back_populates="messages")


class PrincipalRecord(Base):
    """P2-M3a: canonical principal (owner / future guest)."""

    __tablename__ = "principals"
    __table_args__ = (
        Index(
            "uq_principals_single_owner",
            "role",
            unique=True,
            postgresql_where=text("role = 'owner'"),
        ),
        {"schema": DB_SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False, default="owner")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    bindings: Mapped[list[PrincipalBindingRecord]] = relationship(
        back_populates="principal", cascade="all, delete-orphan"
    )


class PrincipalBindingRecord(Base):
    """P2-M3a: channel identity → principal binding."""

    __tablename__ = "principal_bindings"
    __table_args__ = (
        UniqueConstraint("channel_type", "channel_identity", name="uq_principal_bindings_channel"),
        {"schema": DB_SCHEMA},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    principal_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey(f"{DB_SCHEMA}.principals.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    channel_type: Mapped[str] = mapped_column(String(32), nullable=False)
    channel_identity: Mapped[str] = mapped_column(String(256), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    principal: Mapped[PrincipalRecord] = relationship(back_populates="bindings")
