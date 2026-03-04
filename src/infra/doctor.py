"""Doctor: runtime diagnostic checks (read-only).

Reuses preflight C2-C10 checks (not C11 reconcile) and adds
doctor-specific D1-D4 checks (standard) and DD1-DD3 (deep).

All checks are read-only — doctor never writes to DB or files.
Output is sanitized: no API keys, tokens, passwords, or full DSNs.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text

from src.constants import DB_SCHEMA
from src.infra.health import CheckResult, CheckStatus, DoctorReport
from src.infra.preflight import (
    _check_active_provider,
    _check_budget_tables,
    _check_db_connection,
    _check_schema_tables,
    _check_search_trigger,
    _check_soul_versions_readable,
    _check_workspace_dirs,
    _check_workspace_path_consistency,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from src.config.settings import Settings

logger = structlog.get_logger()


def _count_curated_sections(content: str) -> int:
    """Count ## header sections with non-empty body.

    Must match MemoryIndexer._split_by_headers() + index_curated_memory()
    filtering logic: only sections whose body.strip() is truthy are counted.
    """
    if not content.strip():
        return 0

    count = 0
    current_title = ""
    body_lines: list[str] = []

    for line in content.split("\n"):
        if line.startswith("## "):
            if (current_title or body_lines) and "\n".join(body_lines).strip():
                count += 1
            current_title = line[3:].strip()
            body_lines = []
        elif line.startswith("# ") and not current_title:
            current_title = line[2:].strip()
            body_lines = []
        else:
            body_lines.append(line)

    if (current_title or body_lines) and "\n".join(body_lines).strip():
        count += 1

    return count


async def run_doctor(
    settings: Settings, db_engine: AsyncEngine, *, deep: bool = False
) -> DoctorReport:
    """Execute diagnostic checks and return a structured report.

    Standard mode: preflight C2-C10 + D1-D4.
    Deep mode (opt-in): adds DD1-DD3 (external connectivity probes).
    """
    checks: list[CheckResult] = []

    # ── Reuse preflight C2-C10 (not C11 reconcile — doctor is read-only) ──
    checks.append(_check_active_provider(settings))
    checks.append(_check_workspace_path_consistency(settings))
    checks.append(_check_workspace_dirs(settings))

    db_result = await _check_db_connection(db_engine)
    checks.append(db_result)

    if db_result.status != CheckStatus.FAIL:
        checks.append(await _check_schema_tables(db_engine))
        checks.append(await _check_search_trigger(db_engine))
        checks.append(await _check_budget_tables(db_engine))
        checks.append(await _check_soul_versions_readable(db_engine))

    # Telegram check (only when enabled)
    if settings.telegram.bot_token:
        from src.infra.preflight import _check_telegram_connector

        checks.append(await _check_telegram_connector(settings))

    # ── Doctor-specific checks (D1-D4) ──
    if db_result.status != CheckStatus.FAIL:
        checks.append(await _check_soul_consistency(settings, db_engine))
        checks.append(await _check_memory_index_health(settings, db_engine))
        checks.append(await _check_budget_status(db_engine))
        checks.append(await _check_session_activity(db_engine))

    # ── Deep checks (DD1-DD3, opt-in) ──
    if deep:
        checks.append(await _check_provider_connectivity(settings))
        if settings.telegram.bot_token:
            checks.append(await _check_telegram_deep(settings))
        if db_result.status != CheckStatus.FAIL:
            checks.append(await _check_memory_reindex_dryrun(settings, db_engine))

    report = DoctorReport(checks=checks, deep=deep)

    for c in checks:
        if c.status == CheckStatus.WARN:
            logger.warning("doctor_warn", check=c.name, evidence=c.evidence)
        elif c.status == CheckStatus.FAIL:
            logger.error("doctor_fail", check=c.name, evidence=c.evidence)

    logger.info("doctor_complete", passed=report.passed, deep=deep)
    return report


# ── D1: SOUL consistency (read-only) ──


async def _check_soul_consistency(
    settings: Settings, engine: AsyncEngine
) -> CheckResult:
    """D1: Compare DB active soul_versions with workspace/SOUL.md (read-only)."""
    try:
        async with engine.connect() as conn:
            # Check active version count
            result = await conn.execute(
                text(
                    f"SELECT id, version, content FROM {DB_SCHEMA}.soul_versions "
                    f"WHERE status = 'active' ORDER BY version DESC"
                )
            )
            active_rows = result.fetchall()

        if len(active_rows) == 0:
            return CheckResult(
                name="soul_consistency",
                status=CheckStatus.OK,
                evidence="No active soul version in DB (bootstrap not yet done)",
                impact="",
                next_action="",
            )

        if len(active_rows) > 1:
            versions = [str(r[1]) for r in active_rows]
            return CheckResult(
                name="soul_consistency",
                status=CheckStatus.WARN,
                evidence=f"Multiple active versions: {', '.join(versions)}",
                impact=(
                    "Ambiguous SOUL state — latest version is used"
                    " but older active records remain"
                ),
                next_action="Investigate soul_versions table for duplicate active records",
            )

        db_content = active_rows[0][2]
        db_version = active_rows[0][1]

        soul_path = settings.workspace_dir.resolve() / "SOUL.md"
        if not soul_path.is_file():
            return CheckResult(
                name="soul_consistency",
                status=CheckStatus.WARN,
                evidence=f"SOUL.md not found at {soul_path}; DB has active v{db_version}",
                impact="SOUL.md projection missing — agent may lack personality context",
                next_action="Run 'just reconcile' to recreate SOUL.md from DB",
            )

        file_content = soul_path.read_text(encoding="utf-8")
        if file_content.strip() == db_content.strip():
            return CheckResult(
                name="soul_consistency",
                status=CheckStatus.OK,
                evidence=f"SOUL.md matches DB active v{db_version}",
                impact="",
                next_action="",
            )

        # Compute a simple diff summary
        db_lines = db_content.strip().splitlines()
        file_lines = file_content.strip().splitlines()
        diff_lines = abs(len(db_lines) - len(file_lines))
        return CheckResult(
            name="soul_consistency",
            status=CheckStatus.WARN,
            evidence=(
                f"SOUL.md differs from DB active v{db_version} "
                f"(DB: {len(db_lines)} lines, file: {len(file_lines)} lines, "
                f"delta: {diff_lines} lines)"
            ),
            impact="SOUL.md is stale — agent uses file content which may not match DB SSOT",
            next_action="Run 'just reconcile' to sync SOUL.md from DB",
        )
    except Exception as e:
        return CheckResult(
            name="soul_consistency",
            status=CheckStatus.WARN,
            evidence=f"SOUL consistency check failed: {type(e).__name__}",
            impact="Cannot verify SOUL.md state",
            next_action="Check database connectivity and soul_versions table",
        )


# ── D2: memory index health ──


async def _check_memory_index_health(
    settings: Settings, engine: AsyncEngine
) -> CheckResult:
    """D2: Compare memory_entries row count with workspace file entry count."""
    try:
        # Count DB rows
        async with engine.connect() as conn:
            result = await conn.execute(
                text(f"SELECT COUNT(*) FROM {DB_SCHEMA}.memory_entries")
            )
            db_count = result.scalar() or 0

        # Count workspace entries (split by '---' separator, matching indexer logic)
        ws_path = settings.memory.workspace_path.resolve()
        file_count = 0

        memory_dir = ws_path / "memory"
        if memory_dir.is_dir():
            for filepath in sorted(memory_dir.glob("*.md")):
                content = filepath.read_text(encoding="utf-8").strip()
                if content:
                    sections = re.split(r"^---$", content, flags=re.MULTILINE)
                    file_count += sum(1 for s in sections if s.strip())

        memory_md = ws_path / "MEMORY.md"
        if memory_md.is_file():
            content = memory_md.read_text(encoding="utf-8").strip()
            if content:
                file_count += _count_curated_sections(content)

        if db_count == file_count:
            return CheckResult(
                name="memory_index_health",
                status=CheckStatus.OK,
                evidence=f"Index count matches: {db_count} entries",
                impact="",
                next_action="",
            )

        return CheckResult(
            name="memory_index_health",
            status=CheckStatus.WARN,
            evidence=f"Index mismatch: DB has {db_count} entries, files have {file_count} entries",
            impact="Memory search may return stale or incomplete results",
            next_action="Run 'just reindex' to rebuild memory index",
        )
    except Exception as e:
        return CheckResult(
            name="memory_index_health",
            status=CheckStatus.WARN,
            evidence=f"Memory index check failed: {type(e).__name__}",
            impact="Cannot verify memory index state",
            next_action="Check database connectivity and memory_entries table",
        )


# ── D3: budget cumulative state ──


async def _check_budget_status(engine: AsyncEngine) -> CheckResult:
    """D3: Check cumulative budget vs thresholds."""
    try:
        from src.gateway.budget_gate import BUDGET_STOP_EUR, BUDGET_WARN_EUR

        async with engine.connect() as conn:
            result = await conn.execute(
                text(f"SELECT cumulative_eur FROM {DB_SCHEMA}.budget_state")
            )
            row = result.fetchone()

        if row is None:
            return CheckResult(
                name="budget_status",
                status=CheckStatus.OK,
                evidence="No budget state row (budget tracking not initialized)",
                impact="",
                next_action="",
            )

        cumulative = float(row[0])
        remaining_to_warn = BUDGET_WARN_EUR - cumulative
        remaining_to_stop = BUDGET_STOP_EUR - cumulative

        if cumulative >= BUDGET_STOP_EUR:
            return CheckResult(
                name="budget_status",
                status=CheckStatus.FAIL,
                evidence=(
                    f"Budget exhausted: €{cumulative:.2f} >= stop €{BUDGET_STOP_EUR:.2f}"
                ),
                impact="All LLM requests will be denied",
                next_action="Review budget usage and adjust BUDGET_STOP_EUR if needed",
            )

        if cumulative >= BUDGET_WARN_EUR:
            return CheckResult(
                name="budget_status",
                status=CheckStatus.WARN,
                evidence=(
                    f"Budget warning: €{cumulative:.2f} "
                    f"(€{remaining_to_stop:.2f} until stop)"
                ),
                impact="Approaching budget limit — requests may be denied soon",
                next_action="Monitor usage; consider adjusting budget thresholds",
            )

        return CheckResult(
            name="budget_status",
            status=CheckStatus.OK,
            evidence=(
                f"Budget OK: €{cumulative:.2f} "
                f"(€{remaining_to_warn:.2f} until warn, "
                f"€{remaining_to_stop:.2f} until stop)"
            ),
            impact="",
            next_action="",
        )
    except Exception as e:
        return CheckResult(
            name="budget_status",
            status=CheckStatus.WARN,
            evidence=f"Budget check failed: {type(e).__name__}",
            impact="Cannot verify budget state",
            next_action="Check budget_state table existence",
        )


# ── D4: session activity ──


async def _check_session_activity(engine: AsyncEngine) -> CheckResult:
    """D4: Check for hung sessions (processing_since too old)."""
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    f"SELECT id, processing_since FROM {DB_SCHEMA}.sessions "
                    f"WHERE processing_since IS NOT NULL "
                    f"AND processing_since < NOW() - INTERVAL '10 minutes'"
                )
            )
            hung = result.fetchall()

        if not hung:
            return CheckResult(
                name="session_activity",
                status=CheckStatus.OK,
                evidence="No hung sessions detected",
                impact="",
                next_action="",
            )

        session_ids = [r[0][:16] + "..." for r in hung]  # truncate for readability
        return CheckResult(
            name="session_activity",
            status=CheckStatus.WARN,
            evidence=f"{len(hung)} hung session(s): {', '.join(session_ids)}",
            impact="These sessions may be stuck — new requests to them will be rejected",
            next_action="Investigate and clear processing_since for affected sessions",
        )
    except Exception as e:
        return CheckResult(
            name="session_activity",
            status=CheckStatus.WARN,
            evidence=f"Session activity check failed: {type(e).__name__}",
            impact="Cannot verify session state",
            next_action="Check sessions table",
        )


# ── DD1: provider connectivity (deep) ──


async def _check_provider_connectivity(settings: Settings) -> CheckResult:
    """DD1: Test active provider API with a minimal request."""
    try:
        import asyncio

        from openai import AsyncOpenAI

        active = settings.provider.active
        if active == "openai":
            api_key = settings.openai.api_key
            base_url = settings.openai.base_url
            model = settings.openai.model
        elif active == "gemini":
            api_key = settings.gemini.api_key
            base_url = settings.gemini.base_url
            model = settings.gemini.model
        else:
            return CheckResult(
                name="provider_connectivity",
                status=CheckStatus.FAIL,
                evidence=f"Unknown provider '{active}'",
                impact="Cannot test provider connectivity",
                next_action="Set PROVIDER_ACTIVE to 'openai' or 'gemini'",
            )

        if not api_key:
            return CheckResult(
                name="provider_connectivity",
                status=CheckStatus.FAIL,
                evidence=f"Provider '{active}' API key is empty",
                impact="Cannot test connectivity without API key",
                next_action=f"Set {active.upper()}_API_KEY environment variable",
            )

        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        try:
            await asyncio.wait_for(
                client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=1,
                ),
                timeout=15.0,
            )
            return CheckResult(
                name="provider_connectivity",
                status=CheckStatus.OK,
                evidence=f"Provider '{active}' reachable (model={model})",
                impact="",
                next_action="",
            )
        finally:
            await client.close()
    except TimeoutError:
        return CheckResult(
            name="provider_connectivity",
            status=CheckStatus.WARN,
            evidence=f"Provider '{active}' timed out (15s)",
            impact="LLM requests may be slow or fail",
            next_action="Check network connectivity and provider status",
        )
    except Exception as e:
        return CheckResult(
            name="provider_connectivity",
            status=CheckStatus.WARN,
            evidence=f"Provider connectivity failed: {type(e).__name__}",
            impact="LLM requests may fail",
            next_action="Check API key and provider configuration",
        )


# ── DD2: Telegram connector deep check ──


async def _check_telegram_deep(settings: Settings) -> CheckResult:
    """DD2: Re-run Telegram check_ready() (only when enabled)."""
    try:
        from aiogram import Bot

        bot = Bot(token=settings.telegram.bot_token)
        try:
            me = await bot.get_me()
            username = me.username or "(no username)"
            return CheckResult(
                name="telegram_deep",
                status=CheckStatus.OK,
                evidence=f"Telegram bot reachable: @{username}",
                impact="",
                next_action="",
            )
        finally:
            await bot.session.close()
    except Exception as e:
        return CheckResult(
            name="telegram_deep",
            status=CheckStatus.WARN,
            evidence=f"Telegram deep check failed: {type(e).__name__}",
            impact="Telegram channel may not function",
            next_action="Check TELEGRAM_BOT_TOKEN and network connectivity",
        )


# ── DD3: memory reindex dry-run ──


async def _check_memory_reindex_dryrun(
    settings: Settings, engine: AsyncEngine
) -> CheckResult:
    """DD3: Scan workspace files and compare with memory_entries per-file (read-only)."""
    try:
        ws_path = settings.memory.workspace_path.resolve()

        # Collect expected source paths and entry counts from files
        expected: dict[str, int] = {}
        memory_dir = ws_path / "memory"
        if memory_dir.is_dir():
            for filepath in sorted(memory_dir.glob("*.md")):
                content = filepath.read_text(encoding="utf-8").strip()
                if not content:
                    continue
                sections = re.split(r"^---$", content, flags=re.MULTILINE)
                count = sum(1 for s in sections if s.strip())
                if count:
                    rel = str(filepath.relative_to(ws_path))
                    expected[rel] = count

        memory_md = ws_path / "MEMORY.md"
        if memory_md.is_file():
            content = memory_md.read_text(encoding="utf-8").strip()
            if content:
                count = _count_curated_sections(content)
                if count:
                    expected["MEMORY.md"] = count

        # Query DB for per-source_path counts
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    f"SELECT source_path, COUNT(*) FROM {DB_SCHEMA}.memory_entries "
                    f"GROUP BY source_path"
                )
            )
            db_counts: dict[str, int] = {r[0]: r[1] for r in result.fetchall() if r[0]}

        # Compare
        mismatches: list[str] = []
        for path, exp_count in expected.items():
            db_count = db_counts.get(path, 0)
            if db_count != exp_count:
                mismatches.append(f"{path}: file={exp_count}, db={db_count}")

        # Check for orphan paths in DB
        orphans = set(db_counts.keys()) - set(expected.keys())
        for path in orphans:
            mismatches.append(f"{path}: file=0, db={db_counts[path]} (orphan)")

        if not mismatches:
            return CheckResult(
                name="memory_reindex_dryrun",
                status=CheckStatus.OK,
                evidence=f"All {len(expected)} source files match DB index",
                impact="",
                next_action="",
            )

        return CheckResult(
            name="memory_reindex_dryrun",
            status=CheckStatus.WARN,
            evidence=f"{len(mismatches)} mismatch(es): {'; '.join(mismatches[:5])}",
            impact="Memory search results may be incomplete or stale",
            next_action="Run 'just reindex' to rebuild memory index",
        )
    except Exception as e:
        return CheckResult(
            name="memory_reindex_dryrun",
            status=CheckStatus.WARN,
            evidence=f"Reindex dry-run failed: {type(e).__name__}",
            impact="Cannot verify memory index per-file consistency",
            next_action="Check database and workspace file access",
        )
