"""Tests for src/infra/doctor.py — doctor diagnostic checks.

Every check is tested in isolation with mocks. All doctor checks are
read-only: mock DB sessions assert no INSERT/UPDATE/DELETE calls.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.doctor import (
    _check_budget_status,
    _check_memory_index_health,
    _check_memory_reindex_dryrun,
    _check_provider_connectivity,
    _check_session_activity,
    _check_soul_consistency,
    _check_telegram_deep,
    _count_curated_sections,
    run_doctor,
)
from src.infra.health import CheckStatus, DoctorReport

# ── Helpers ──


def _make_settings(**overrides: object) -> MagicMock:
    """Build a mock Settings object with sane defaults."""
    ws = overrides.pop("workspace_dir", Path("/tmp/test_ws"))
    settings = MagicMock()
    settings.workspace_dir = ws
    settings.memory.workspace_path = overrides.pop("memory_workspace_path", ws)
    settings.provider.active = overrides.pop("provider_active", "openai")
    settings.openai.api_key = overrides.pop("openai_api_key", "sk-test")
    settings.openai.base_url = overrides.pop("openai_base_url", None)
    settings.openai.model = overrides.pop("openai_model", "gpt-4o-mini")
    settings.gemini.api_key = overrides.pop("gemini_api_key", "")
    settings.gemini.base_url = overrides.pop("gemini_base_url", "https://example.com")
    settings.gemini.model = overrides.pop("gemini_model", "gemini-2.5-flash")
    settings.telegram.bot_token = overrides.pop("telegram_bot_token", "")
    settings.database.schema_ = "neomagi"
    return settings


def _async_ctx(obj: object) -> MagicMock:
    """Wrap obj in an async context manager mock."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=obj)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _mock_engine_with_responses(responses: dict[str, object]) -> AsyncMock:
    """Create a mock engine that returns different responses based on query content."""
    conn = AsyncMock()

    async def _execute(stmt, params=None):
        result = MagicMock()
        stmt_str = str(stmt) if not hasattr(stmt, "text") else str(stmt.text)
        for key, value in responses.items():
            if key in stmt_str:
                if isinstance(value, Exception):
                    raise value
                if isinstance(value, list):
                    result.fetchall.return_value = value
                    result.fetchone.return_value = value[0] if value else None
                    result.scalar.return_value = value[0][0] if value and value[0] else 0
                elif value is None:
                    result.fetchall.return_value = []
                    result.fetchone.return_value = None
                    result.scalar.return_value = 0
                else:
                    result.fetchall.return_value = [(value,)]
                    result.fetchone.return_value = (value,)
                    result.scalar.return_value = value
                return result
        # Default: return empty
        result.fetchall.return_value = []
        result.fetchone.return_value = None
        result.scalar.return_value = 0
        return result

    conn.execute = _execute
    engine = AsyncMock()
    engine.connect = MagicMock(return_value=_async_ctx(conn))
    return engine


def _mock_engine_all_ok() -> AsyncMock:
    """Create a mock engine that returns OK responses for all check queries."""
    tables = ["sessions", "messages", "memory_entries", "soul_versions"]
    budget_tables = ["budget_state", "budget_reservations"]

    conn = AsyncMock()

    async def _execute(stmt, params=None):
        result = MagicMock()
        stmt_str = str(stmt) if not hasattr(stmt, "text") else str(stmt.text)

        if "information_schema.tables" in stmt_str:
            if "budget" in stmt_str:
                result.fetchall.return_value = [(t,) for t in budget_tables]
            else:
                result.fetchall.return_value = [(t,) for t in tables]
        elif "information_schema.triggers" in stmt_str:
            result.fetchone.return_value = (1,)
        elif "soul_versions" in stmt_str and "status = 'active'" in stmt_str:
            result.fetchall.return_value = [(1, 0, "soul content")]
        elif "soul_versions" in stmt_str:
            result.fetchone.return_value = (1,)
        elif "COUNT" in stmt_str and "memory_entries" in stmt_str:
            result.scalar.return_value = 0
        elif "cumulative_eur" in stmt_str:
            result.fetchone.return_value = (5.0,)
        elif "processing_since" in stmt_str:
            result.fetchall.return_value = []
        elif "source_path" in stmt_str:
            result.fetchall.return_value = []
        else:
            result.fetchone.return_value = (1,)
            result.scalar.return_value = 1
        return result

    conn.execute = _execute
    engine = AsyncMock()
    engine.connect = MagicMock(return_value=_async_ctx(conn))
    return engine


# ── D1: soul_consistency ──


class TestCheckSoulConsistency:
    @pytest.mark.asyncio
    async def test_no_active_version(self) -> None:
        engine = _mock_engine_with_responses({"soul_versions": None})
        s = _make_settings()
        r = await _check_soul_consistency(s, engine)
        assert r.status == CheckStatus.OK
        assert "No active" in r.evidence

    @pytest.mark.asyncio
    async def test_multiple_active(self) -> None:
        engine = _mock_engine_with_responses({
            "soul_versions": [(1, 2, "v2 content"), (2, 1, "v1 content")]
        })
        s = _make_settings()
        r = await _check_soul_consistency(s, engine)
        assert r.status == CheckStatus.WARN
        assert "Multiple active" in r.evidence

    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path: Path) -> None:
        engine = _mock_engine_with_responses({
            "soul_versions": [(1, 0, "content")]
        })
        # workspace_dir exists but SOUL.md doesn't
        s = _make_settings(workspace_dir=tmp_path)
        r = await _check_soul_consistency(s, engine)
        assert r.status == CheckStatus.WARN
        assert "not found" in r.evidence

    @pytest.mark.asyncio
    async def test_consistent(self, tmp_path: Path) -> None:
        soul_content = "# My SOUL"
        (tmp_path / "SOUL.md").write_text(soul_content)
        engine = _mock_engine_with_responses({
            "soul_versions": [(1, 0, soul_content)]
        })
        s = _make_settings(workspace_dir=tmp_path)
        r = await _check_soul_consistency(s, engine)
        assert r.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_drift(self, tmp_path: Path) -> None:
        (tmp_path / "SOUL.md").write_text("old content")
        engine = _mock_engine_with_responses({
            "soul_versions": [(1, 0, "new content\nwith more lines")]
        })
        s = _make_settings(workspace_dir=tmp_path)
        r = await _check_soul_consistency(s, engine)
        assert r.status == CheckStatus.WARN
        assert "differs" in r.evidence

    @pytest.mark.asyncio
    async def test_exception_is_warn(self) -> None:
        engine = AsyncMock()
        engine.connect = MagicMock(side_effect=RuntimeError("boom"))
        s = _make_settings()
        r = await _check_soul_consistency(s, engine)
        assert r.status == CheckStatus.WARN


# ── _count_curated_sections helper ──


class TestCountCuratedSections:
    def test_empty(self) -> None:
        assert _count_curated_sections("") == 0
        assert _count_curated_sections("   ") == 0

    def test_h2_sections(self) -> None:
        content = "## Section A\nBody A\n## Section B\nBody B\n## Section C\nBody C"
        assert _count_curated_sections(content) == 3

    def test_h1_plus_h2(self) -> None:
        content = "# Title\n\n## Section A\nBody A\n## Section B\nBody B"
        assert _count_curated_sections(content) == 2

    def test_empty_body_skipped(self) -> None:
        content = "## Has Body\nSome text\n## Empty\n## Also Body\nMore text"
        assert _count_curated_sections(content) == 2

    def test_no_headers(self) -> None:
        """Content without headers → one section (matches _split_by_headers)."""
        assert _count_curated_sections("Just plain text") == 1

    def test_triple_dash_not_counted(self) -> None:
        """--- separators should NOT split curated memory sections."""
        content = "## Section A\nBody A\n---\nMore body A\n## Section B\nBody B"
        # --- is just body content, so 2 sections total
        assert _count_curated_sections(content) == 2


# ── D2: memory_index_health ──


class TestCheckMemoryIndexHealth:
    @pytest.mark.asyncio
    async def test_counts_match(self, tmp_path: Path) -> None:
        # Create workspace with 2 entries in a daily note
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (mem_dir / "2026-01-01.md").write_text("entry1\n---\nentry2")

        engine = _mock_engine_with_responses({"COUNT": [(2,)]})
        s = _make_settings(workspace_dir=tmp_path, memory_workspace_path=tmp_path)
        r = await _check_memory_index_health(s, engine)
        assert r.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_counts_mismatch(self, tmp_path: Path) -> None:
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (mem_dir / "2026-01-01.md").write_text("entry1\n---\nentry2")

        engine = _mock_engine_with_responses({"COUNT": [(5,)]})
        s = _make_settings(workspace_dir=tmp_path, memory_workspace_path=tmp_path)
        r = await _check_memory_index_health(s, engine)
        assert r.status == CheckStatus.WARN
        assert "mismatch" in r.evidence

    @pytest.mark.asyncio
    async def test_no_files(self, tmp_path: Path) -> None:
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        engine = _mock_engine_with_responses({"COUNT": [(0,)]})
        s = _make_settings(workspace_dir=tmp_path, memory_workspace_path=tmp_path)
        r = await _check_memory_index_health(s, engine)
        assert r.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_memory_md_uses_header_split(self, tmp_path: Path) -> None:
        """MEMORY.md must be counted by ## headers, not --- separators."""
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        # MEMORY.md with 2 ## sections (no --- separators)
        (tmp_path / "MEMORY.md").write_text(
            "# Title\n\n## Section A\nBody A\n## Section B\nBody B"
        )
        # DB has 2 entries matching the 2 header sections
        engine = _mock_engine_with_responses({"COUNT": [(2,)]})
        s = _make_settings(workspace_dir=tmp_path, memory_workspace_path=tmp_path)
        r = await _check_memory_index_health(s, engine)
        assert r.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_memory_md_with_dashes_in_body(self, tmp_path: Path) -> None:
        """--- inside MEMORY.md body should not inflate the count."""
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        # MEMORY.md: 2 sections, one containing --- in body
        (tmp_path / "MEMORY.md").write_text(
            "## Section A\nBody A\n---\nStill body A\n## Section B\nBody B"
        )
        engine = _mock_engine_with_responses({"COUNT": [(2,)]})
        s = _make_settings(workspace_dir=tmp_path, memory_workspace_path=tmp_path)
        r = await _check_memory_index_health(s, engine)
        assert r.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_exception_is_warn(self) -> None:
        engine = AsyncMock()
        engine.connect = MagicMock(side_effect=RuntimeError("boom"))
        s = _make_settings()
        r = await _check_memory_index_health(s, engine)
        assert r.status == CheckStatus.WARN


# ── D3: budget_status ──


class TestCheckBudgetStatus:
    @pytest.mark.asyncio
    async def test_ok(self) -> None:
        engine = _mock_engine_with_responses({"cumulative_eur": [(5.0,)]})
        r = await _check_budget_status(engine)
        assert r.status == CheckStatus.OK
        assert "5.00" in r.evidence

    @pytest.mark.asyncio
    async def test_warn(self) -> None:
        engine = _mock_engine_with_responses({"cumulative_eur": [(21.0,)]})
        r = await _check_budget_status(engine)
        assert r.status == CheckStatus.WARN

    @pytest.mark.asyncio
    async def test_fail_at_stop(self) -> None:
        engine = _mock_engine_with_responses({"cumulative_eur": [(25.0,)]})
        r = await _check_budget_status(engine)
        assert r.status == CheckStatus.FAIL
        assert "exhausted" in r.evidence

    @pytest.mark.asyncio
    async def test_no_budget_row(self) -> None:
        engine = _mock_engine_with_responses({"cumulative_eur": None})
        r = await _check_budget_status(engine)
        assert r.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_exception_is_warn(self) -> None:
        engine = AsyncMock()
        engine.connect = MagicMock(side_effect=RuntimeError("boom"))
        r = await _check_budget_status(engine)
        assert r.status == CheckStatus.WARN


# ── D4: session_activity ──


class TestCheckSessionActivity:
    @pytest.mark.asyncio
    async def test_no_hung(self) -> None:
        engine = _mock_engine_with_responses({"processing_since": []})
        r = await _check_session_activity(engine)
        assert r.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_hung_sessions(self) -> None:
        engine = _mock_engine_with_responses({
            "processing_since": [("sess-abc-123456789", "2026-01-01T00:00:00")]
        })
        r = await _check_session_activity(engine)
        assert r.status == CheckStatus.WARN
        assert "hung" in r.evidence

    @pytest.mark.asyncio
    async def test_exception_is_warn(self) -> None:
        engine = AsyncMock()
        engine.connect = MagicMock(side_effect=RuntimeError("boom"))
        r = await _check_session_activity(engine)
        assert r.status == CheckStatus.WARN


# ── DD1: provider_connectivity ──


class TestCheckProviderConnectivity:
    @pytest.mark.asyncio
    async def test_ok(self) -> None:
        s = _make_settings()
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=MagicMock())
        mock_client.close = AsyncMock()

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            r = await _check_provider_connectivity(s)
        assert r.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_unknown_provider(self) -> None:
        s = _make_settings(provider_active="unknown")
        r = await _check_provider_connectivity(s)
        assert r.status == CheckStatus.FAIL

    @pytest.mark.asyncio
    async def test_empty_key(self) -> None:
        s = _make_settings(openai_api_key="")
        r = await _check_provider_connectivity(s)
        assert r.status == CheckStatus.FAIL

    @pytest.mark.asyncio
    async def test_timeout(self) -> None:
        s = _make_settings()
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=TimeoutError())
        mock_client.close = AsyncMock()

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            with patch("asyncio.wait_for", side_effect=TimeoutError()):
                r = await _check_provider_connectivity(s)
        assert r.status == CheckStatus.WARN
        assert "timed out" in r.evidence

    @pytest.mark.asyncio
    async def test_api_error(self) -> None:
        s = _make_settings()
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=Exception("401"))
        mock_client.close = AsyncMock()

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            with patch("asyncio.wait_for", side_effect=Exception("401")):
                r = await _check_provider_connectivity(s)
        assert r.status == CheckStatus.WARN


# ── DD2: telegram_deep ──


class TestCheckTelegramDeep:
    @pytest.mark.asyncio
    async def test_ok(self) -> None:
        s = _make_settings(telegram_bot_token="123:ABC")
        mock_bot = AsyncMock()
        mock_me = MagicMock()
        mock_me.username = "testbot"
        mock_bot.get_me = AsyncMock(return_value=mock_me)
        mock_bot.session = MagicMock()
        mock_bot.session.close = AsyncMock()

        with patch("aiogram.Bot", return_value=mock_bot):
            r = await _check_telegram_deep(s)
        assert r.status == CheckStatus.OK
        assert "testbot" in r.evidence

    @pytest.mark.asyncio
    async def test_fail(self) -> None:
        s = _make_settings(telegram_bot_token="bad")
        mock_bot = AsyncMock()
        mock_bot.get_me = AsyncMock(side_effect=Exception("Unauthorized"))
        mock_bot.session = MagicMock()
        mock_bot.session.close = AsyncMock()

        with patch("aiogram.Bot", return_value=mock_bot):
            r = await _check_telegram_deep(s)
        assert r.status == CheckStatus.WARN


# ── DD3: memory_reindex_dryrun ──


class TestCheckMemoryReindexDryrun:
    @pytest.mark.asyncio
    async def test_all_match(self, tmp_path: Path) -> None:
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (mem_dir / "2026-01-01.md").write_text("entry1\n---\nentry2")

        engine = _mock_engine_with_responses({
            "source_path": [("memory/2026-01-01.md", 2)]
        })
        s = _make_settings(workspace_dir=tmp_path, memory_workspace_path=tmp_path)
        r = await _check_memory_reindex_dryrun(s, engine)
        assert r.status == CheckStatus.OK

    @pytest.mark.asyncio
    async def test_mismatch(self, tmp_path: Path) -> None:
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (mem_dir / "2026-01-01.md").write_text("entry1\n---\nentry2")

        engine = _mock_engine_with_responses({
            "source_path": [("memory/2026-01-01.md", 5)]
        })
        s = _make_settings(workspace_dir=tmp_path, memory_workspace_path=tmp_path)
        r = await _check_memory_reindex_dryrun(s, engine)
        assert r.status == CheckStatus.WARN
        assert "mismatch" in r.evidence

    @pytest.mark.asyncio
    async def test_orphan_detected(self, tmp_path: Path) -> None:
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        # No files, but DB has entries
        engine = _mock_engine_with_responses({
            "source_path": [("memory/old-file.md", 3)]
        })
        s = _make_settings(workspace_dir=tmp_path, memory_workspace_path=tmp_path)
        r = await _check_memory_reindex_dryrun(s, engine)
        assert r.status == CheckStatus.WARN
        assert "orphan" in r.evidence

    @pytest.mark.asyncio
    async def test_memory_md_uses_header_split(self, tmp_path: Path) -> None:
        """DD3 must count MEMORY.md by ## headers, not --- separators."""
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (tmp_path / "MEMORY.md").write_text(
            "## Section A\nBody A\n## Section B\nBody B"
        )
        # DB has 2 entries for MEMORY.md matching the 2 header sections
        engine = _mock_engine_with_responses({
            "source_path": [("MEMORY.md", 2)]
        })
        s = _make_settings(workspace_dir=tmp_path, memory_workspace_path=tmp_path)
        r = await _check_memory_reindex_dryrun(s, engine)
        assert r.status == CheckStatus.OK


# ── run_doctor composite ──


class TestRunDoctor:
    @pytest.mark.asyncio
    async def test_standard_mode(self, tmp_path: Path) -> None:
        """Standard doctor includes preflight + D1-D4, no deep checks."""
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        settings = _make_settings(workspace_dir=tmp_path, memory_workspace_path=tmp_path)
        engine = _mock_engine_all_ok()

        report = await run_doctor(settings, engine, deep=False)

        check_names = [c.name for c in report.checks]
        # Should have preflight checks
        assert "active_provider" in check_names
        assert "db_connection" in check_names
        # Should have doctor checks
        assert "soul_consistency" in check_names
        assert "memory_index_health" in check_names
        assert "budget_status" in check_names
        assert "session_activity" in check_names
        # Should NOT have deep checks
        assert "provider_connectivity" not in check_names
        assert "telegram_deep" not in check_names
        assert "memory_reindex_dryrun" not in check_names
        assert report.deep is False

    @pytest.mark.asyncio
    async def test_deep_mode(self, tmp_path: Path) -> None:
        """Deep doctor includes DD1-DD3."""
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        settings = _make_settings(workspace_dir=tmp_path, memory_workspace_path=tmp_path)
        engine = _mock_engine_all_ok()

        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=MagicMock())
        mock_client.close = AsyncMock()

        with patch("openai.AsyncOpenAI", return_value=mock_client):
            report = await run_doctor(settings, engine, deep=True)

        check_names = [c.name for c in report.checks]
        assert "provider_connectivity" in check_names
        assert report.deep is True

    @pytest.mark.asyncio
    async def test_db_fail_skips_doctor_checks(self) -> None:
        """DB connection fail → doctor D1-D4 and DD3 are skipped."""
        settings = _make_settings()
        engine = AsyncMock()
        engine.connect = MagicMock(side_effect=ConnectionRefusedError("refused"))

        report = await run_doctor(settings, engine, deep=False)

        check_names = [c.name for c in report.checks]
        assert "db_connection" in check_names
        # Doctor checks that need DB should be absent
        assert "soul_consistency" not in check_names
        assert "memory_index_health" not in check_names
        assert "budget_status" not in check_names
        assert "session_activity" not in check_names

    @pytest.mark.asyncio
    async def test_read_only_guarantee(self, tmp_path: Path) -> None:
        """Doctor checks must not call INSERT/UPDATE/DELETE on DB."""
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        settings = _make_settings(workspace_dir=tmp_path, memory_workspace_path=tmp_path)

        # Track all SQL statements executed
        executed_stmts: list[str] = []

        conn = AsyncMock()

        async def _tracking_execute(stmt, params=None):
            stmt_str = str(stmt) if not hasattr(stmt, "text") else str(stmt.text)
            executed_stmts.append(stmt_str)
            result = MagicMock()
            result.fetchall.return_value = []
            result.fetchone.return_value = None
            result.scalar.return_value = 0
            return result

        conn.execute = _tracking_execute
        engine = AsyncMock()
        engine.connect = MagicMock(return_value=_async_ctx(conn))

        await run_doctor(settings, engine, deep=False)

        # Verify no write operations
        for stmt in executed_stmts:
            stmt_upper = stmt.upper()
            assert "INSERT" not in stmt_upper, f"Doctor executed INSERT: {stmt}"
            assert "UPDATE" not in stmt_upper, f"Doctor executed UPDATE: {stmt}"
            assert "DELETE" not in stmt_upper, f"Doctor executed DELETE: {stmt}"


# ── DoctorReport model ──


class TestDoctorReport:
    def test_passed_no_fails(self) -> None:
        from src.infra.health import CheckResult

        report = DoctorReport(
            checks=[
                CheckResult("a", CheckStatus.OK, "", "", ""),
                CheckResult("b", CheckStatus.WARN, "", "", ""),
            ]
        )
        assert report.passed is True

    def test_failed_with_fail(self) -> None:
        from src.infra.health import CheckResult

        report = DoctorReport(
            checks=[
                CheckResult("a", CheckStatus.OK, "", "", ""),
                CheckResult("b", CheckStatus.FAIL, "broken", "", ""),
            ]
        )
        assert report.passed is False

    def test_summary_format(self) -> None:
        from src.infra.health import CheckResult

        report = DoctorReport(
            checks=[CheckResult("test", CheckStatus.OK, "all good", "", "")],
            deep=True,
        )
        s = report.summary()
        assert "doctor PASS" in s
        assert "mode=deep" in s
