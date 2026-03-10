"""Preflight runner: unified startup checks with structured evidence output.

Replaces scattered lifespan checks with a single ``run_preflight()`` call
that produces a ``PreflightReport``.  Each check yields a ``CheckResult``
(OK / WARN / FAIL) so the caller can decide whether to block startup.
"""

from __future__ import annotations

import tempfile
from typing import TYPE_CHECKING

import structlog
from sqlalchemy import text

from src.constants import DB_SCHEMA
from src.infra.health import CheckResult, CheckStatus, PreflightReport

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

    from src.config.settings import Settings

logger = structlog.get_logger()

# Tables that must exist for core operation
_REQUIRED_TABLES = frozenset({
    "sessions",
    "messages",
    "memory_entries",
    "soul_versions",
})

_BUDGET_TABLES = frozenset({
    "budget_state",
    "budget_reservations",
})


async def run_preflight(settings: Settings, db_engine: AsyncEngine) -> PreflightReport:
    """Execute all preflight checks and return a structured report.

    Check order mirrors dependency: config → filesystem → DB → connectors → reconcile.
    """
    checks: list[CheckResult] = []

    checks.append(_check_active_provider(settings))
    checks.append(_check_workspace_path_consistency(settings))
    checks.append(_check_workspace_dirs(settings))

    db_result = await _check_db_connection(db_engine)
    checks.append(db_result)

    # DB-dependent checks only run if connection succeeded
    if db_result.status != CheckStatus.FAIL:
        checks.append(await _check_schema_tables(db_engine))
        checks.append(await _check_search_trigger(db_engine))
        checks.append(await _check_budget_tables(db_engine))
        checks.append(await _check_soul_versions_readable(db_engine))

    # Telegram check (only when enabled)
    if settings.telegram.bot_token:
        checks.append(await _check_telegram_connector(settings))

    # SOUL.md reconcile (only when DB is reachable)
    if db_result.status != CheckStatus.FAIL:
        checks.append(await _check_soul_reconcile(settings, db_engine))

    report = PreflightReport(checks=checks)

    for c in checks:
        if c.status == CheckStatus.WARN:
            logger.warning("preflight_warn", check=c.name, evidence=c.evidence)
        elif c.status == CheckStatus.FAIL:
            logger.error("preflight_fail", check=c.name, evidence=c.evidence)

    return report


async def run_readiness_checks(settings: Settings, db_engine: AsyncEngine) -> PreflightReport:
    """Execute lightweight runtime checks for /health/ready (no side effects).

    Subset of preflight: C3-C9 only. Excludes:
    - C2 (static config, doesn't change after startup)
    - C10 (external API call to Telegram)
    - C11 (has write side effects)
    """
    checks: list[CheckResult] = []

    checks.append(_check_workspace_path_consistency(settings))
    checks.append(_check_workspace_dirs(settings))

    db_result = await _check_db_connection(db_engine)
    checks.append(db_result)

    if db_result.status != CheckStatus.FAIL:
        checks.append(await _check_schema_tables(db_engine))
        checks.append(await _check_search_trigger(db_engine))
        checks.append(await _check_budget_tables(db_engine))
        checks.append(await _check_soul_versions_readable(db_engine))

    return PreflightReport(checks=checks)


# ── C2: active provider configuration ──


def _check_active_provider(settings: Settings) -> CheckResult:
    """C2: Verify that the active provider has a non-empty API key."""
    active = settings.provider.active
    if active == "openai":
        has_key = bool(settings.openai.api_key)
    elif active == "gemini":
        has_key = bool(settings.gemini.api_key)
    else:
        return CheckResult(
            name="active_provider",
            status=CheckStatus.FAIL,
            evidence=f"Unknown provider '{active}'",
            impact="No LLM provider available; all requests will fail",
            next_action="Set PROVIDER_ACTIVE to 'openai' or 'gemini'",
        )

    if has_key:
        return CheckResult(
            name="active_provider",
            status=CheckStatus.OK,
            evidence=f"Provider '{active}' API key configured",
            impact="",
            next_action="",
        )
    return CheckResult(
        name="active_provider",
        status=CheckStatus.FAIL,
        evidence=f"Provider '{active}' API key is empty",
        impact="No LLM provider available; all requests will fail",
        next_action=f"Set {active.upper()}_API_KEY environment variable",
    )


# ── C3: workspace_dir / memory.workspace_path consistency ──


def _check_workspace_path_consistency(settings: Settings) -> CheckResult:
    """C3: workspace_dir and memory.workspace_path must resolve to the same path."""
    ws_dir = settings.workspace_dir.resolve()
    mem_ws = settings.memory.workspace_path.resolve()
    if ws_dir == mem_ws:
        return CheckResult(
            name="workspace_path_consistency",
            status=CheckStatus.OK,
            evidence=f"Both resolve to {ws_dir}",
            impact="",
            next_action="",
        )
    return CheckResult(
        name="workspace_path_consistency",
        status=CheckStatus.FAIL,
        evidence=f"workspace_dir={ws_dir}, memory.workspace_path={mem_ws}",
        impact="Memory subsystem will read/write wrong directory",
        next_action="Align MEMORY_WORKSPACE_PATH with workspace_dir. See ADR 0037.",
    )


# ── C4: workspace necessary dirs exist and writable ──


def _check_workspace_dirs(settings: Settings) -> CheckResult:
    """C4: workspace and workspace/memory/ must exist and be writable."""
    ws = settings.workspace_dir.resolve()
    for directory, label in [(ws, "Workspace dir"), (ws / "memory", "memory/ subdirectory")]:
        fail = _check_dir_writable(directory, label)
        if fail is not None:
            return fail
    return CheckResult(name="workspace_dirs", status=CheckStatus.OK,
                       evidence="Workspace and memory/ exist and are writable",
                       impact="", next_action="")


def _check_dir_writable(directory, label: str) -> CheckResult | None:
    """Return a FAIL CheckResult if dir missing or not writable, else None."""
    if not directory.is_dir():
        return CheckResult(
            name="workspace_dirs", status=CheckStatus.FAIL,
            evidence=f"{label} does not exist: {directory}",
            impact=f"Cannot read/write {label.lower()} files",
            next_action=f"Create {directory} or fix configuration",
        )
    try:
        with tempfile.NamedTemporaryFile(dir=directory, delete=True):
            pass
    except OSError as e:
        return CheckResult(
            name="workspace_dirs", status=CheckStatus.FAIL,
            evidence=f"{label} not writable: {e}",
            impact=f"Cannot write to {label.lower()}",
            next_action=f"Fix filesystem permissions on {directory}",
        )
    return None


# ── C5: DB connection ──


async def _check_db_connection(engine: AsyncEngine) -> CheckResult:
    """C5: Verify database is reachable."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return CheckResult(
            name="db_connection",
            status=CheckStatus.OK,
            evidence="Database connection successful",
            impact="",
            next_action="",
        )
    except Exception as e:
        return CheckResult(
            name="db_connection",
            status=CheckStatus.FAIL,
            evidence=f"Database connection failed: {type(e).__name__}",
            impact="All persistence operations will fail",
            next_action="Check DATABASE_* environment variables and PostgreSQL availability",
        )


# ── C6: schema tables ──


async def _check_schema_tables(engine: AsyncEngine) -> CheckResult:
    """C6: Verify required tables exist in the neomagi schema."""
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :schema"
                ),
                {"schema": DB_SCHEMA},
            )
            existing = {row[0] for row in result.fetchall()}

        missing = _REQUIRED_TABLES - existing
        if not missing:
            return CheckResult(
                name="schema_tables",
                status=CheckStatus.OK,
                evidence=f"All required tables present in {DB_SCHEMA}",
                impact="",
                next_action="",
            )
        return CheckResult(
            name="schema_tables",
            status=CheckStatus.FAIL,
            evidence=f"Missing tables: {', '.join(sorted(missing))}",
            impact="Core persistence operations will fail",
            next_action="Run ensure_schema() or check migration state",
        )
    except Exception as e:
        return CheckResult(
            name="schema_tables",
            status=CheckStatus.FAIL,
            evidence=f"Schema introspection failed: {type(e).__name__}",
            impact="Cannot verify schema state",
            next_action="Check database connectivity and permissions",
        )


# ── C7: search trigger ──


async def _check_search_trigger(engine: AsyncEngine) -> CheckResult:
    """C7: Verify search vector trigger exists (WARN, not FAIL)."""
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT 1 FROM information_schema.triggers "
                    "WHERE trigger_schema = :schema "
                    "AND trigger_name = 'trg_memory_entries_search_vector'"
                ),
                {"schema": DB_SCHEMA},
            )
            if result.fetchone():
                return CheckResult(
                    name="search_trigger",
                    status=CheckStatus.OK,
                    evidence="Search vector trigger exists",
                    impact="",
                    next_action="",
                )
        return CheckResult(
            name="search_trigger",
            status=CheckStatus.WARN,
            evidence="Search vector trigger missing",
            impact="Memory search may return stale or empty results",
            next_action="Run ensure_schema() to recreate the trigger",
        )
    except Exception as e:
        return CheckResult(
            name="search_trigger",
            status=CheckStatus.WARN,
            evidence=f"Trigger check failed: {type(e).__name__}",
            impact="Cannot verify search trigger state",
            next_action="Check database connectivity",
        )


# ── C8: budget tables ──


async def _check_budget_tables(engine: AsyncEngine) -> CheckResult:
    """C8: Verify budget_state and budget_reservations tables exist (FAIL)."""
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :schema "
                    "AND table_name IN ('budget_state', 'budget_reservations')"
                ),
                {"schema": DB_SCHEMA},
            )
            existing = {row[0] for row in result.fetchall()}

        missing = _BUDGET_TABLES - existing
        if not missing:
            return CheckResult(
                name="budget_tables",
                status=CheckStatus.OK,
                evidence="Budget tables present",
                impact="",
                next_action="",
            )
        return CheckResult(
            name="budget_tables",
            status=CheckStatus.FAIL,
            evidence=f"Missing budget tables: {', '.join(sorted(missing))}",
            impact="BudgetGate.try_reserve() will crash on first request",
            next_action="Create budget tables (see ADR 0041 migration)",
        )
    except Exception as e:
        return CheckResult(
            name="budget_tables",
            status=CheckStatus.FAIL,
            evidence=f"Budget table check failed: {type(e).__name__}",
            impact="Cannot verify budget gate readiness",
            next_action="Check database connectivity",
        )


# ── C9: soul_versions readable ──


async def _check_soul_versions_readable(engine: AsyncEngine) -> CheckResult:
    """C9: Verify soul_versions table is readable."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f"SELECT 1 FROM {DB_SCHEMA}.soul_versions LIMIT 1"))
        return CheckResult(
            name="soul_versions_readable",
            status=CheckStatus.OK,
            evidence="soul_versions table is readable",
            impact="",
            next_action="",
        )
    except Exception as e:
        return CheckResult(
            name="soul_versions_readable",
            status=CheckStatus.FAIL,
            evidence=f"soul_versions read failed: {type(e).__name__}",
            impact="SOUL.md evolution engine will not function",
            next_action="Check schema migration state for soul_versions table",
        )


# ── C10: Telegram connector ──


async def _check_telegram_connector(settings: Settings) -> CheckResult:
    """C10: Verify Telegram bot token via getMe (only when enabled)."""
    try:
        from aiogram import Bot

        bot = Bot(token=settings.telegram.bot_token)
        try:
            me = await bot.get_me()
            username = me.username or "(no username)"
            return CheckResult(
                name="telegram_connector",
                status=CheckStatus.OK,
                evidence=f"Telegram bot authenticated: @{username}",
                impact="",
                next_action="",
            )
        finally:
            await bot.session.close()
    except Exception as e:
        return CheckResult(
            name="telegram_connector",
            status=CheckStatus.FAIL,
            evidence=f"Telegram auth failed: {type(e).__name__}",
            impact="Telegram channel will not function",
            next_action="Check TELEGRAM_BOT_TOKEN environment variable",
        )


# ── C11: SOUL.md projection reconcile ──


async def _check_soul_reconcile(settings: Settings, db_engine: AsyncEngine) -> CheckResult:
    """C11: Run SOUL.md reconciliation (startup context, write is allowed)."""
    try:
        from src.memory.evolution import EvolutionEngine
        from src.session.database import make_session_factory

        db_factory = make_session_factory(db_engine)
        evo = EvolutionEngine(db_factory, settings.workspace_dir, settings.memory)
        await evo.reconcile_soul_projection()
        return CheckResult(
            name="soul_reconcile",
            status=CheckStatus.OK,
            evidence="SOUL.md projection reconciled successfully",
            impact="",
            next_action="",
        )
    except Exception as e:
        return CheckResult(
            name="soul_reconcile",
            status=CheckStatus.WARN,
            evidence=f"SOUL.md reconcile issue: {type(e).__name__}: {e}",
            impact="SOUL.md may be stale relative to DB",
            next_action="Run 'just reconcile' manually after startup",
        )
