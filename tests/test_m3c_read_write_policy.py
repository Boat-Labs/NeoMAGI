"""Tests for P2-M3c Slice D (read path hardening) + Slice E (write path can_write).

Covers:
- Searcher SQL visibility WHERE (DB integration)
- PromptBuilder _filter_entries V1 policy equivalence
- Ledger can_write policy guard + shared_space_id rejection
- Writer VisibilityPolicyError propagation
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.prompt_builder import PromptBuilder
from src.constants import DB_SCHEMA
from src.infra.errors import VisibilityPolicyError
from src.memory.models import MemoryEntry
from src.memory.query_processor import segment_for_index
from src.memory.searcher import MemorySearcher
from src.memory.visibility import MEMORY_VISIBILITY_POLICY_VERSION

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_prompt_builder() -> PromptBuilder:
    settings = MagicMock()
    settings.workspace_path = MagicMock()
    settings.max_daily_note_bytes = 100_000
    return PromptBuilder(
        workspace_dir=MagicMock(),
        settings=settings,
        tool_registry=MagicMock(),
    )


def _daily_entry(
    text: str,
    *,
    scope: str = "main",
    principal: str | None = None,
    visibility: str | None = None,
) -> str:
    """Build a daily note entry string with metadata line."""
    parts = [f"scope: {scope}"]
    if principal:
        parts.append(f"principal: {principal}")
    if visibility:
        parts.append(f"visibility: {visibility}")
    meta = ", ".join(parts)
    return f"[12:00] ({meta})\n{text}"


# ===========================================================================
# Slice D: PromptBuilder _filter_entries V1 policy
# ===========================================================================


class TestPromptFilterPrivate:
    def test_owner_sees_own(self) -> None:
        content = _daily_entry("my data", principal="u1", visibility="private_to_principal")
        result = PromptBuilder._filter_entries(content, "main", principal_id="u1")
        assert "my data" in result

    def test_cross_principal_hidden(self) -> None:
        content = _daily_entry("secret", principal="u2", visibility="private_to_principal")
        result = PromptBuilder._filter_entries(content, "main", principal_id="u1")
        assert result == ""

    def test_anonymous_sees_legacy(self) -> None:
        content = _daily_entry("legacy data")
        result = PromptBuilder._filter_entries(content, "main", principal_id=None)
        assert "legacy data" in result

    def test_anonymous_cannot_see_owned(self) -> None:
        content = _daily_entry("owned data", principal="u1")
        result = PromptBuilder._filter_entries(content, "main", principal_id=None)
        assert result == ""

    def test_authenticated_sees_legacy(self) -> None:
        content = _daily_entry("legacy data")
        result = PromptBuilder._filter_entries(content, "main", principal_id="u1")
        assert "legacy data" in result


class TestPromptFilterSummary:
    def test_same_principal_visible(self) -> None:
        content = _daily_entry("summary", principal="u1", visibility="shareable_summary")
        result = PromptBuilder._filter_entries(content, "main", principal_id="u1")
        assert "summary" in result

    def test_cross_principal_hidden(self) -> None:
        content = _daily_entry("summary", principal="u1", visibility="shareable_summary")
        result = PromptBuilder._filter_entries(content, "main", principal_id="u2")
        assert result == ""

    def test_anonymous_cannot_see_summary(self) -> None:
        content = _daily_entry("summary", principal="u1", visibility="shareable_summary")
        result = PromptBuilder._filter_entries(content, "main", principal_id=None)
        assert result == ""

    def test_no_principal_summary_hidden_for_authenticated(self) -> None:
        """No-principal shareable_summary is not legacy-visible (V1 hardening)."""
        content = _daily_entry("orphan summary", visibility="shareable_summary")
        result = PromptBuilder._filter_entries(content, "main", principal_id="u1")
        assert result == ""

    def test_no_principal_summary_hidden_for_anonymous(self) -> None:
        content = _daily_entry("orphan summary", visibility="shareable_summary")
        result = PromptBuilder._filter_entries(content, "main", principal_id=None)
        assert result == ""


class TestPromptFilterDeny:
    def test_shared_in_space_denied(self) -> None:
        content = _daily_entry("shared", visibility="shared_in_space")
        result = PromptBuilder._filter_entries(content, "main", principal_id="u1")
        assert result == ""

    def test_unknown_visibility_denied(self) -> None:
        content = _daily_entry("unknown", visibility="weird_value")
        result = PromptBuilder._filter_entries(content, "main", principal_id="u1")
        assert result == ""

    def test_no_visibility_treated_as_private(self) -> None:
        """No visibility metadata → private_to_principal (legacy compatible)."""
        content = _daily_entry("legacy")
        result = PromptBuilder._filter_entries(content, "main", principal_id="u1")
        assert "legacy" in result


# ===========================================================================
# Slice D: Searcher SQL visibility (DB integration)
# ===========================================================================


@pytest_asyncio.fixture
async def _clean_entries(db_session_factory):
    async with db_session_factory() as db:
        await db.execute(text(f"DELETE FROM {DB_SCHEMA}.memory_entries"))
        await db.commit()
    yield


async def _insert_entry(
    db: AsyncSession, content: str, *,
    principal_id: str | None = None,
    visibility: str = "private_to_principal",
    scope_key: str = "main",
) -> None:
    entry = MemoryEntry(
        scope_key=scope_key,
        source_type="daily_note",
        title="",
        content=content,
        search_text=segment_for_index(content),
        tags=[],
        principal_id=principal_id,
        visibility=visibility,
    )
    db.add(entry)


@pytest.mark.integration
class TestSearcherVisibility:
    async def test_owner_sees_own_and_legacy(
        self, db_session_factory, _clean_entries,
    ) -> None:
        async with db_session_factory() as db:
            await _insert_entry(db, "owner memory data here", principal_id="u1")
            await _insert_entry(db, "legacy memory data here")
            await db.commit()

        searcher = MemorySearcher(db_session_factory, MagicMock())
        results = await searcher.search("memory data", principal_id="u1")
        assert len(results) == 2

    async def test_cross_principal_hidden(
        self, db_session_factory, _clean_entries,
    ) -> None:
        async with db_session_factory() as db:
            await _insert_entry(db, "secret memory data", principal_id="u2")
            await db.commit()

        searcher = MemorySearcher(db_session_factory, MagicMock())
        results = await searcher.search("memory data", principal_id="u1")
        assert len(results) == 0

    async def test_anonymous_sees_only_legacy(
        self, db_session_factory, _clean_entries,
    ) -> None:
        async with db_session_factory() as db:
            await _insert_entry(db, "legacy memory data")
            await _insert_entry(db, "owned memory data", principal_id="u1")
            await db.commit()

        searcher = MemorySearcher(db_session_factory, MagicMock())
        results = await searcher.search("memory data", principal_id=None)
        assert len(results) == 1
        assert results[0].principal_id is None

    async def test_shared_in_space_excluded(
        self, db_session_factory, _clean_entries,
    ) -> None:
        async with db_session_factory() as db:
            await _insert_entry(
                db, "shared memory data", principal_id="u1",
                visibility="shared_in_space",
            )
            await db.commit()

        searcher = MemorySearcher(db_session_factory, MagicMock())
        results = await searcher.search("memory data", principal_id="u1")
        assert len(results) == 0

    async def test_shareable_summary_same_principal_only(
        self, db_session_factory, _clean_entries,
    ) -> None:
        async with db_session_factory() as db:
            await _insert_entry(
                db, "shareable summary data", principal_id="u1",
                visibility="shareable_summary",
            )
            await db.commit()

        searcher = MemorySearcher(db_session_factory, MagicMock())
        # Owner can see
        r1 = await searcher.search("summary data", principal_id="u1")
        assert len(r1) == 1
        # Other principal cannot
        r2 = await searcher.search("summary data", principal_id="u2")
        assert len(r2) == 0
        # Anonymous cannot
        r3 = await searcher.search("summary data", principal_id=None)
        assert len(r3) == 0


# ===========================================================================
# Slice E: Ledger can_write policy
# ===========================================================================


class TestLedgerVisibilityPolicy:
    async def test_shared_in_space_rejected(self) -> None:
        from src.memory.ledger import MemoryLedgerWriter

        ledger = MemoryLedgerWriter(MagicMock())
        with pytest.raises(VisibilityPolicyError, match="shared_space_policy_not_implemented"):
            await ledger.append(
                entry_id="e1", content="test", visibility="shared_in_space",
            )

    async def test_shared_space_id_in_metadata_rejected(self) -> None:
        from src.memory.ledger import MemoryLedgerWriter

        ledger = MemoryLedgerWriter(MagicMock())
        with pytest.raises(VisibilityPolicyError, match="membership_unavailable"):
            await ledger.append(
                entry_id="e2", content="test",
                visibility="private_to_principal",
                metadata={"shared_space_id": "space-1"},
            )

    async def test_unknown_visibility_rejected(self) -> None:
        from src.memory.ledger import MemoryLedgerWriter

        ledger = MemoryLedgerWriter(MagicMock())
        with pytest.raises(VisibilityPolicyError, match="unknown_visibility_value"):
            await ledger.append(
                entry_id="e3", content="test", visibility="weird",
            )

    async def test_private_to_principal_allowed(self) -> None:
        """Normal write should pass policy and attempt DB insert."""
        from src.memory.ledger import MemoryLedgerWriter

        mock_factory = MagicMock()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = ("event-id",)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()
        mock_factory.return_value = mock_session

        ledger = MemoryLedgerWriter(mock_factory)
        result = await ledger.append(entry_id="e4", content="test")
        assert result is True

    async def test_metadata_none_passes_rule_0(self) -> None:
        """metadata=None should not trigger shared_space_id guard."""
        from src.memory.ledger import MemoryLedgerWriter

        mock_factory = MagicMock()
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.fetchone.return_value = ("event-id",)
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock()
        mock_factory.return_value = mock_session

        ledger = MemoryLedgerWriter(mock_factory)
        result = await ledger.append(
            entry_id="e5", content="test", metadata=None,
        )
        assert result is True


class TestWriterVisibilityPolicy:
    async def test_shared_in_space_rejected(self) -> None:
        from src.memory.writer import MemoryWriter

        writer = MemoryWriter(
            workspace_path=MagicMock(),
            settings=MagicMock(),
        )
        with pytest.raises(VisibilityPolicyError):
            await writer.append_daily_note("test", visibility="shared_in_space")

    async def test_unknown_visibility_rejected(self) -> None:
        from src.memory.writer import MemoryWriter

        writer = MemoryWriter(
            workspace_path=MagicMock(),
            settings=MagicMock(),
        )
        with pytest.raises(VisibilityPolicyError):
            await writer.append_daily_note("test", visibility="unknown_value")

    async def test_writer_denial_log_fields(self) -> None:
        """Writer denial log includes principal_id, visibility, reason."""
        import io

        import structlog

        from src.memory.writer import MemoryWriter

        captured = io.StringIO()
        structlog.configure(
            processors=[structlog.dev.ConsoleRenderer()],
            wrapper_class=structlog.make_filtering_bound_logger(0),
            logger_factory=structlog.PrintLoggerFactory(file=captured),
        )
        try:
            writer = MemoryWriter(
                workspace_path=MagicMock(),
                settings=MagicMock(),
            )
            with pytest.raises(VisibilityPolicyError):
                await writer.append_daily_note(
                    "test", visibility="shared_in_space",
                    principal_id="audit-user",
                )
            output = captured.getvalue()
            assert "visibility_policy_denied" in output
            assert "audit-user" in output
            assert "shared_space_policy_not_implemented" in output
        finally:
            structlog.reset_defaults()

    async def test_visibility_policy_error_is_memory_write_error(self) -> None:
        """VisibilityPolicyError must be catchable as MemoryWriteError."""
        from src.infra.errors import MemoryWriteError
        err = VisibilityPolicyError("test")
        assert isinstance(err, MemoryWriteError)


class TestPolicyVersionExported:
    def test_version_constant(self) -> None:
        assert MEMORY_VISIBILITY_POLICY_VERSION == "v1"
