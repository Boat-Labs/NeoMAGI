"""NeoMAGI CLI entry point.

Usage:
  python -m src.backend.cli doctor [--deep]
  python -m src.backend.cli reindex [--scope main]
  python -m src.backend.cli reconcile
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import structlog

from src.infra.logging import setup_logging

logger = structlog.get_logger()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="neomagi",
        description="NeoMAGI CLI — operational diagnostics and recovery",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    doctor_parser = sub.add_parser("doctor", help="Run diagnostic checks")
    doctor_parser.add_argument(
        "--deep",
        action="store_true",
        help="Run deep checks (provider connectivity, Telegram, reindex dry-run)",
    )

    reindex_parser = sub.add_parser("reindex", help="TRUNCATE + full reindex of memory_entries")
    reindex_parser.add_argument(
        "--scope",
        default="main",
        help="Scope key for reindex (default: main)",
    )

    sub.add_parser("reconcile", help="Reconcile SOUL.md projection from DB")

    return parser


async def _run_doctor(deep: bool) -> int:
    """Execute doctor checks and print formatted results."""
    from src.config.settings import get_settings
    from src.infra.doctor import run_doctor
    from src.session.database import create_db_engine

    settings = get_settings()
    engine = await create_db_engine(settings.database)
    try:
        report = await run_doctor(settings, engine, deep=deep)
    finally:
        await engine.dispose()

    print(report.summary())  # noqa: T201 — CLI output
    logger.info("doctor_cli_done", passed=report.passed, deep=deep)
    return 0 if report.passed else 1


async def _run_reindex(scope_key: str) -> int:
    """TRUNCATE memory_entries then reindex_all from workspace files."""
    from sqlalchemy import text

    from src.config.settings import get_settings
    from src.constants import DB_SCHEMA
    from src.memory.indexer import MemoryIndexer
    from src.session.database import create_db_engine, make_session_factory

    settings = get_settings()
    engine = await create_db_engine(settings.database)
    try:
        # TRUNCATE first — clear orphan entries from deleted files
        async with engine.begin() as conn:
            result = await conn.execute(
                text(f"SELECT COUNT(*) FROM {DB_SCHEMA}.memory_entries")
            )
            old_count = result.scalar() or 0
            await conn.execute(text(f"TRUNCATE {DB_SCHEMA}.memory_entries"))
        logger.info("reindex_truncated", cleared=old_count)

        # Reindex from workspace files
        session_factory = make_session_factory(engine)
        indexer = MemoryIndexer(session_factory, settings.memory)
        new_count = await indexer.reindex_all(scope_key=scope_key)
        logger.info("reindex_done", new_entries=new_count, scope=scope_key)

        print(f"Reindex complete: cleared {old_count} → rebuilt {new_count} entries")  # noqa: T201
    finally:
        await engine.dispose()

    return 0


async def _run_reconcile() -> int:
    """Reconcile SOUL.md projection from DB truth-source."""
    from src.config.settings import get_settings
    from src.memory.evolution import EvolutionEngine
    from src.session.database import create_db_engine, make_session_factory

    settings = get_settings()
    engine = await create_db_engine(settings.database)
    try:
        session_factory = make_session_factory(engine)
        evolution = EvolutionEngine(
            session_factory, settings.memory.workspace_path, settings.memory
        )
        await evolution.reconcile_soul_projection()
        logger.info("reconcile_done")
        print("Reconcile complete: SOUL.md synchronized with DB")  # noqa: T201
    except Exception:
        logger.exception("reconcile_failed")
        print("Reconcile failed — see logs for details", file=sys.stderr)  # noqa: T201
        return 1
    finally:
        await engine.dispose()

    return 0


def main() -> None:
    setup_logging(json_output=False)
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    if args.command == "doctor":
        code = asyncio.run(_run_doctor(deep=args.deep))
        sys.exit(code)

    if args.command == "reindex":
        code = asyncio.run(_run_reindex(scope_key=args.scope))
        sys.exit(code)

    if args.command == "reconcile":
        code = asyncio.run(_run_reconcile())
        sys.exit(code)


if __name__ == "__main__":
    main()
