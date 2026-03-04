"""NeoMAGI CLI entry point.

Usage: python -m src.backend.cli doctor [--deep]
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
        description="NeoMAGI CLI — operational diagnostics",
    )
    sub = parser.add_subparsers(dest="command", help="Available commands")

    doctor_parser = sub.add_parser("doctor", help="Run diagnostic checks")
    doctor_parser.add_argument(
        "--deep",
        action="store_true",
        help="Run deep checks (provider connectivity, Telegram, reindex dry-run)",
    )

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


if __name__ == "__main__":
    main()
