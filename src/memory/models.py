"""SQLAlchemy model for memory_entries search index."""

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
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, TSVECTOR

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
