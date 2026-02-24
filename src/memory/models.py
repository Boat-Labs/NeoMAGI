"""SQLAlchemy models for memory subsystem.

Includes:
- MemoryEntry: full-text search index (Phase 2)
- SoulVersionRecord: SOUL.md version history (Phase 4)
"""

from __future__ import annotations

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR

from src.constants import DB_SCHEMA
from src.session.models import Base


class MemoryEntry(Base):
    """Memory entries for full-text search.

    Source of truth: files (daily notes + MEMORY.md).
    This table is a search index only — delete-reinsert on reindex.
    All entries carry scope_key for scope-aware filtering (ADR 0034).
    """

    __tablename__ = "memory_entries"
    __table_args__ = (
        Index("idx_memory_entries_scope", "scope_key"),
        Index(
            "idx_memory_entries_search",
            "search_vector",
            postgresql_using="gin",
        ),
        {"schema": DB_SCHEMA},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    scope_key = Column(String(128), nullable=False, default="main")
    source_type = Column(String(16), nullable=False)  # daily_note | curated | flush_candidate
    source_path = Column(String(256), nullable=True)
    source_date = Column(Date, nullable=True)
    title = Column(Text, nullable=False, default="")
    content = Column(Text, nullable=False)
    tags = Column(ARRAY(Text), default=list)
    confidence = Column(Float, nullable=True)
    search_vector = Column(TSVECTOR, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SoulVersionRecord(Base):
    """SOUL.md version history for evolution governance (ADR 0027).

    Status lifecycle:
    - proposed → (eval passes) → active → superseded (when newer version applied)
    - proposed → vetoed (user rejects proposal)
    - active → rolled_back (user or system rollback)
    """

    __tablename__ = "soul_versions"
    __table_args__ = (
        UniqueConstraint("version", name="uq_soul_versions_version"),
        {"schema": DB_SCHEMA},
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(16), nullable=False)  # active|proposed|superseded|rolled_back|vetoed
    proposal = Column(JSONB, nullable=True)
    eval_result = Column(JSONB, nullable=True)
    created_by = Column(String(32), nullable=False)  # agent|bootstrap|system
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
