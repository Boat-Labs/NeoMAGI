"""Tests for MemoryEntry model."""

from __future__ import annotations

from src.constants import DB_SCHEMA
from src.memory.models import MemoryEntry


class TestMemoryEntryModel:
    def test_tablename(self) -> None:
        assert MemoryEntry.__tablename__ == "memory_entries"

    def test_schema(self) -> None:
        assert MemoryEntry.__table_args__[-1]["schema"] == DB_SCHEMA

    def test_columns_exist(self) -> None:
        cols = {c.name for c in MemoryEntry.__table__.columns}
        expected = {
            "id", "scope_key", "source_type", "source_path", "source_date",
            "title", "content", "tags", "confidence", "search_vector",
            "created_at", "updated_at",
        }
        assert expected.issubset(cols)

    def test_scope_key_default(self) -> None:
        col = MemoryEntry.__table__.c.scope_key
        assert col.default is not None or col.server_default is not None

    def test_has_search_vector_column(self) -> None:
        col = MemoryEntry.__table__.c.search_vector
        assert col is not None
