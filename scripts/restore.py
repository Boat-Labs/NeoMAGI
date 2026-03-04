"""NeoMAGI restore script — 8-step recovery sequence.

Usage: python scripts/restore.py --db-dump <path> --workspace-archive <path>

Steps:
  1. Check pg_restore availability
  2. pg_restore DB truth-source (--clean)
  3. ensure_schema() — guarantee memory_entries table + triggers
  4. Extract workspace memory archive
  5. reconcile_soul_projection() — rebuild SOUL.md from DB
  6. TRUNCATE memory_entries — clear stale index
  7. MemoryIndexer.reindex_all() — rebuild search index
  8. run_preflight() — verify restored state
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import structlog

from src.infra.logging import setup_logging

logger = structlog.get_logger()


def _check_pg_restore() -> str:
    """Return pg_restore path or exit with guidance."""
    path = shutil.which("pg_restore")
    if not path:
        logger.error("pg_restore_not_found")
        print(  # noqa: T201
            "ERROR: pg_restore not found. Install PostgreSQL client utilities:\n"
            "  macOS:  brew install libpq && brew link --force libpq\n"
            "  Debian: apt install postgresql-client-16\n"
            "  Arch:   pacman -S postgresql-libs",
            file=sys.stderr,
        )
        sys.exit(1)
    return path


def _get_dsn() -> str:
    from src.config.settings import get_settings

    db = get_settings().database
    password_part = f":{db.password}" if db.password else ""
    return f"postgresql://{db.user}{password_part}@{db.host}:{db.port}/{db.name}"


async def run_restore(db_dump: Path, workspace_archive: Path) -> None:
    from sqlalchemy import text

    from src.config.settings import get_settings
    from src.constants import DB_SCHEMA
    from src.infra.preflight import run_preflight
    from src.memory.evolution import EvolutionEngine
    from src.memory.indexer import MemoryIndexer
    from src.session.database import create_db_engine, ensure_schema, make_session_factory

    results: list[tuple[str, str]] = []  # (step, status)

    # --- Step 1: Check pg_restore ---
    pg_restore = _check_pg_restore()
    results.append(("1. pg_restore check", "OK"))
    logger.info("restore_step_1_done")

    # --- Step 2: pg_restore DB truth-source ---
    dsn = _get_dsn()
    cmd = [pg_restore, "--clean", "--if-exists", f"--dbname={dsn}", str(db_dump)]
    logger.info("restore_step_2_start", dump=str(db_dump))
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0:
        # pg_restore returns non-zero for warnings too; check stderr for fatal errors
        stderr = proc.stderr.strip()
        if stderr and "error" in stderr.lower():
            logger.error("pg_restore_failed", stderr=stderr)
            results.append(("2. pg_restore DB", f"FAIL: {stderr[:200]}"))
            _print_summary(results)
            sys.exit(1)
        logger.warning("pg_restore_warnings", stderr=stderr)
    results.append(("2. pg_restore DB", "OK"))
    logger.info("restore_step_2_done")

    # --- Step 3: ensure_schema ---
    settings = get_settings()
    engine = await create_db_engine(settings.database)
    try:
        await ensure_schema(engine, DB_SCHEMA)
        results.append(("3. ensure_schema", "OK"))
        logger.info("restore_step_3_done")

        # --- Step 4: Extract workspace archive ---
        logger.info("restore_step_4_start", archive=str(workspace_archive))
        proc = subprocess.run(
            ["tar", "xzf", str(workspace_archive)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            logger.error("tar_extract_failed", stderr=proc.stderr)
            results.append(("4. Extract workspace", f"FAIL: {proc.stderr[:200]}"))
            _print_summary(results)
            sys.exit(1)
        results.append(("4. Extract workspace", "OK"))
        logger.info("restore_step_4_done")

        # --- Step 5: reconcile_soul_projection ---
        session_factory = make_session_factory(engine)
        workspace_path = settings.memory.workspace_path
        evolution = EvolutionEngine(session_factory, workspace_path, settings.memory)
        reconcile_changed = False
        try:
            await evolution.reconcile_soul_projection()
            reconcile_changed = True
        except Exception:
            logger.exception("reconcile_failed")
            reconcile_changed = False
        results.append(("5. reconcile SOUL.md", "OK" if reconcile_changed else "WARN (no-op)"))
        logger.info("restore_step_5_done", changed=reconcile_changed)

        # --- Step 6: TRUNCATE memory_entries ---
        async with engine.begin() as conn:
            await conn.execute(text(f"TRUNCATE {DB_SCHEMA}.memory_entries"))
        results.append(("6. TRUNCATE memory_entries", "OK"))
        logger.info("restore_step_6_done")

        # --- Step 7: reindex_all ---
        indexer = MemoryIndexer(session_factory, settings.memory)
        entry_count = await indexer.reindex_all()
        results.append(("7. reindex_all", f"OK ({entry_count} entries)"))
        logger.info("restore_step_7_done", entries=entry_count)

        # --- Step 8: run_preflight ---
        report = await run_preflight(settings, engine)
        if report.passed:
            results.append(("8. preflight", "PASS"))
        else:
            fail_names = [c.name for c in report.checks if c.status.value == "fail"]
            results.append(("8. preflight", f"FAIL: {', '.join(fail_names)}"))
        logger.info("restore_step_8_done", passed=report.passed)
    finally:
        await engine.dispose()

    _print_summary(results, report=report)
    if not report.passed:
        print(  # noqa: T201
            "\nWARNING: Preflight contains FAIL items. "
            "Review above and fix before starting the service.",
            file=sys.stderr,
        )
        sys.exit(1)


def _print_summary(
    results: list[tuple[str, str]],
    *,
    report: object | None = None,
) -> None:
    print("\n=== Restore Summary ===")  # noqa: T201
    for step, status in results:
        print(f"  {step}: {status}")  # noqa: T201
    if report is not None and hasattr(report, "summary"):
        print(f"\n{report.summary()}")  # noqa: T201


def main() -> None:
    setup_logging(json_output=False)
    parser = argparse.ArgumentParser(description="NeoMAGI restore — 8-step recovery sequence")
    parser.add_argument(
        "--db-dump",
        type=Path,
        required=True,
        help="Path to pg_dump custom-format backup file",
    )
    parser.add_argument(
        "--workspace-archive",
        type=Path,
        required=True,
        help="Path to workspace memory tar.gz archive",
    )
    args = parser.parse_args()

    if not args.db_dump.exists():
        print(f"ERROR: DB dump not found: {args.db_dump}", file=sys.stderr)  # noqa: T201
        sys.exit(1)
    if not args.workspace_archive.exists():
        print(  # noqa: T201
            f"ERROR: Workspace archive not found: {args.workspace_archive}",
            file=sys.stderr,
        )
        sys.exit(1)

    asyncio.run(run_restore(args.db_dump, args.workspace_archive))


if __name__ == "__main__":
    main()
