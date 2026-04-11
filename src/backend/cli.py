"""NeoMAGI CLI entry point.

Usage:
  python -m src.backend.cli doctor [--deep]
  python -m src.backend.cli init-soul [--from path]
  python -m src.backend.cli reindex [--scope main]
  python -m src.backend.cli reset-user-db [--yes]
  python -m src.backend.cli reconcile
  python -m src.backend.cli check-governance-tables
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import structlog
from alembic.config import Config

from alembic import command
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

    init_soul_parser = sub.add_parser(
        "init-soul",
        help="Explicitly seed SOUL.md from the default template or a custom path",
    )
    init_soul_parser.add_argument(
        "--from",
        dest="source",
        help="Optional source file to copy into workspace/SOUL.md before bootstrap",
    )

    reset_user_db_parser = sub.add_parser(
        "reset-user-db",
        help="Drop the user schema and rebuild it from Alembic migrations",
    )
    reset_user_db_parser.add_argument(
        "--yes",
        action="store_true",
        help="Required confirmation flag for destructive reset",
    )

    sub.add_parser("reconcile", help="Reconcile SOUL.md projection from DB")

    sub.add_parser(
        "check-governance-tables",
        help="Show row counts for governance tables (skills, wrappers, soul)",
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


def _run_alembic_upgrade_head() -> None:
    """Rebuild schema from the Alembic head revision."""
    alembic_ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    command.upgrade(Config(str(alembic_ini)), "head")


async def _run_reset_user_db(confirm: bool) -> int:
    """Drop the user schema and rebuild it from migrations."""
    from src.config.settings import DatabaseSettings

    db = DatabaseSettings()
    target = f"{db.host}:{db.port}/{db.name} schema={db.schema_}"
    if not confirm:
        print(f"reset-user-db refused: this will permanently delete all data in {target}.")  # noqa: T201
        print("Rerun with --yes to confirm.")  # noqa: T201
        return 1

    logger.warning("reset_user_db_started", database=db.name, schema=db.schema_, host=db.host)
    print(f"reset-user-db: dropping schema on {target}")  # noqa: T201

    await _drop_schema(db)
    rc = await _rebuild_schema(db)
    if rc != 0:
        return rc

    logger.info("reset_user_db_done", database=db.name, schema=db.schema_)
    print(f"reset-user-db complete: rebuilt {target} from a blank schema")  # noqa: T201
    return 0


async def _drop_schema(db) -> None:
    """Drop the user schema."""
    from sqlalchemy import text

    from src.session.database import create_db_engine

    engine = await create_db_engine(db)
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {db.schema_} CASCADE"))
    finally:
        await engine.dispose()


async def _rebuild_schema(db) -> int:
    """Run Alembic upgrade + ensure_schema. Returns 0 on success, 1 on failure."""
    from src.session.database import create_db_engine, ensure_schema

    try:
        _run_alembic_upgrade_head()
    except Exception:
        logger.exception("reset_user_db_alembic_failed", database=db.name, schema=db.schema_)
        print("reset-user-db failed during Alembic rebuild -- see logs", file=sys.stderr)  # noqa: T201
        return 1
    engine = await create_db_engine(db)
    try:
        await ensure_schema(engine, db.schema_)
    except Exception:
        logger.exception("reset_user_db_ensure_schema_failed", database=db.name, schema=db.schema_)
        print("reset-user-db failed during schema verification -- see logs", file=sys.stderr)  # noqa: T201
        return 1
    finally:
        await engine.dispose()
    return 0


async def _run_check_governance_tables() -> int:
    """Print row counts for all governance-related tables."""
    from sqlalchemy import text

    from src.config.settings import get_settings
    from src.session.database import create_db_engine

    settings = get_settings()
    schema = settings.database.schema_
    tables = [
        "soul_versions",
        "skill_specs",
        "skill_evidence",
        "skill_spec_versions",
        "wrapper_tools",
        "wrapper_tool_versions",
    ]

    engine = await create_db_engine(settings.database)
    try:
        print(  # noqa: T201
            f"db={settings.database.name} host={settings.database.host} schema={schema}"
        )
        async with engine.connect() as conn:
            for name in tables:
                count = (
                    await conn.execute(
                        text(f"SELECT COUNT(*) FROM {schema}.{name}")
                    )
                ).scalar_one()
                print(f"  {name}: {count}")  # noqa: T201
    finally:
        await engine.dispose()

    return 0


def _default_soul_template_path() -> Path:
    """Return the repo-local default SOUL template path."""
    return Path(__file__).resolve().parents[2] / "design_docs" / "templates" / "SOUL.default.md"


def _resolve_soul_source_path(source: str | None) -> Path:
    """Resolve explicit init-soul source path."""
    if source:
        raw = Path(source).expanduser()
        return (raw if raw.is_absolute() else (Path.cwd() / raw)).resolve()
    return _default_soul_template_path().resolve()


def _prepare_soul_seed_file(
    soul_path: Path,
    source_content: str,
    *,
    source_requested: bool,
) -> tuple[bool, str]:
    """Ensure workspace/SOUL.md exists with safe, explicit seed semantics."""
    if soul_path.is_file():
        existing_content = soul_path.read_text(encoding="utf-8")
        if existing_content.strip():
            if source_requested and existing_content != source_content:
                raise ValueError(
                    "workspace/SOUL.md already exists and differs from the requested source"
                )
            if source_requested:
                return False, "workspace/SOUL.md already matches the requested source."
            return False, "Using existing workspace/SOUL.md as the explicit seed."

    soul_path.write_text(source_content, encoding="utf-8")
    return True, f"Wrote {soul_path} from the selected source."


def _print_init_soul_banner() -> None:
    """Print operator-facing disclosure before explicit SOUL bootstrap."""
    print("init-soul: explicit SOUL bootstrap")  # noqa: T201
    print(  # noqa: T201
        "This command seeds workspace/SOUL.md once and imports it into the database."
    )
    print(  # noqa: T201
        "After initialization, the database becomes the truth source; "
        "future changes should use soul_propose, soul_rollback, or just reconcile."
    )


def _load_soul_source(source: str | None) -> tuple[Path, str]:
    """Load init-soul source path and content, or raise ValueError."""
    source_path = _resolve_soul_source_path(source)
    if not source_path.is_file():
        raise ValueError(f"source file not found: {source_path}")

    source_content = source_path.read_text(encoding="utf-8")
    if not source_content.strip():
        raise ValueError(f"source file is empty: {source_path}")
    return source_path, source_content


async def _check_init_soul_allowed(evolution) -> bool:
    """Refuse init-soul when DB already has an active SOUL version."""
    current = await evolution.get_current_version()
    if current is None:
        return True

    print(  # noqa: T201
        f"init-soul refused: database already has active SOUL version v{current.version}."
    )
    print("DB remains the truth source. Use soul_propose or soul_rollback instead.")  # noqa: T201
    return False


async def _bootstrap_init_soul(
    evolution,
    workspace_dir: Path,
    soul_path: Path,
    source: str | None,
    source_path: Path,
    source_content: str,
) -> int:
    """Write/import the initial SOUL seed into workspace and DB."""
    workspace_dir.mkdir(parents=True, exist_ok=True)
    try:
        wrote_file, note = _prepare_soul_seed_file(
            soul_path,
            source_content,
            source_requested=source is not None,
        )
    except ValueError as exc:
        print(f"init-soul refused: {exc}", file=sys.stderr)  # noqa: T201
        print("Review or remove workspace/SOUL.md first, then rerun init-soul.")  # noqa: T201
        return 1

    print(f"Seed source: {source_path}")  # noqa: T201
    print(f"Target file: {soul_path}")  # noqa: T201
    print(note)  # noqa: T201

    await evolution.ensure_bootstrap(workspace_dir)
    current = await evolution.get_current_version()
    if current is None:
        print("init-soul failed: bootstrap did not create an active SOUL version.", file=sys.stderr)  # noqa: T201
        return 1

    logger.info(
        "init_soul_done",
        version=current.version,
        source=str(source_path),
        wrote_file=wrote_file,
    )
    print(f"init-soul complete: active SOUL version v{current.version}")  # noqa: T201
    return 0


async def _run_init_soul(source: str | None) -> int:
    """Explicitly seed workspace/SOUL.md and import it as the initial DB version."""
    from src.config.settings import get_settings
    from src.memory.evolution import EvolutionEngine
    from src.session.database import create_db_engine, make_session_factory

    _print_init_soul_banner()

    settings = get_settings()
    workspace_dir = settings.workspace_dir.resolve()
    soul_path = workspace_dir / "SOUL.md"
    try:
        source_path, source_content = _load_soul_source(source)
    except ValueError as exc:
        print(f"init-soul failed: {exc}", file=sys.stderr)  # noqa: T201
        return 1

    engine = await create_db_engine(settings.database)
    try:
        session_factory = make_session_factory(engine)
        evolution = EvolutionEngine(session_factory, workspace_dir, settings.memory)
        if not await _check_init_soul_allowed(evolution):
            return 1
        return await _bootstrap_init_soul(
            evolution,
            workspace_dir,
            soul_path,
            source,
            source_path,
            source_content,
        )
    except Exception:
        logger.exception("init_soul_failed", source=str(source_path))
        print("init-soul failed -- see logs for details", file=sys.stderr)  # noqa: T201
        return 1
    finally:
        await engine.dispose()


def _dispatch(args: argparse.Namespace) -> int:
    """Map parsed CLI args to the corresponding async handler and return exit code."""
    dispatch = {
        "doctor": lambda: _run_doctor(deep=args.deep),
        "init-soul": lambda: _run_init_soul(source=args.source),
        "reindex": lambda: _run_reindex(scope_key=args.scope),
        "reset-user-db": lambda: _run_reset_user_db(confirm=args.yes),
        "reconcile": _run_reconcile,
        "check-governance-tables": _run_check_governance_tables,
    }
    handler = dispatch.get(args.command)
    if handler is None:
        return 1
    return asyncio.run(handler())


def main() -> None:
    setup_logging(json_output=False)
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    sys.exit(_dispatch(args))


if __name__ == "__main__":
    main()
