"""NeoMAGI backup script — exports DB truth-source tables + workspace memory files.

Usage: python scripts/backup.py [--output-dir ./backups]

Requires: pg_dump CLI tool (PostgreSQL client utilities).
Reads DB connection info from .env.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import structlog

from src.infra.logging import setup_logging

logger = structlog.get_logger()

# Truth-source tables (excludes derived memory_entries — rebuilt via reindex)
TRUTH_TABLES = [
    "neomagi.principals",
    "neomagi.principal_bindings",
    "neomagi.sessions",
    "neomagi.messages",
    "neomagi.soul_versions",
    "neomagi.budget_state",
    "neomagi.budget_reservations",
    "neomagi.memory_source_ledger",
]


def _check_pg_dump() -> str:
    """Return pg_dump path or exit with guidance."""
    path = shutil.which("pg_dump")
    if not path:
        logger.error("pg_dump_not_found")
        print(  # noqa: T201
            "ERROR: pg_dump not found. Install PostgreSQL client utilities:\n"
            "  macOS:  brew install libpq && brew link --force libpq\n"
            "  Debian: apt install postgresql-client-16\n"
            "  Arch:   pacman -S postgresql-libs",
            file=sys.stderr,
        )
        sys.exit(1)
    return path


def _get_dsn() -> str:
    """Build PostgreSQL DSN from .env settings."""
    from src.config.settings import get_settings

    db = get_settings().database
    password_part = f":{db.password}" if db.password else ""
    return f"postgresql://{db.user}{password_part}@{db.host}:{db.port}/{db.name}"


def _assert_workspace_path_consistency() -> Path:
    """Fail-fast guard: workspace_dir must equal memory.workspace_path.

    ADR 0037: workspace_dir is the single source of truth.
    backup/restore are standalone CLIs that don't run preflight C3,
    so they must self-check.
    """
    from src.config.settings import get_settings

    settings = get_settings()
    ws = settings.workspace_dir.resolve()
    mem_ws = settings.memory.workspace_path.resolve()
    if ws != mem_ws:
        logger.error(
            "workspace_path_mismatch",
            workspace_dir=str(ws),
            memory_workspace_path=str(mem_ws),
        )
        print(  # noqa: T201
            f"ERROR: workspace_dir ({ws}) != memory.workspace_path ({mem_ws}).\n"
            f"Fix configuration. See ADR 0037.",
            file=sys.stderr,
        )
        sys.exit(1)
    return ws


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _exit_backup_failure(event: str, stderr: str, label: str) -> None:
    logger.error(event, stderr=stderr)
    print(f"ERROR: {label} failed:\n{stderr}", file=sys.stderr)  # noqa: T201
    sys.exit(1)


def _build_table_args() -> list[str]:
    table_args: list[str] = []
    for table in TRUTH_TABLES:
        table_args.extend(["--table", table])
    return table_args


def _run_pg_dump_step(
    pg_dump: str,
    dsn: str,
    dump_file: Path,
) -> None:
    cmd = [pg_dump, *_build_table_args(), "--format=custom", "-f", str(dump_file), dsn]
    logger.info("pg_dump_start", tables=TRUTH_TABLES, output=str(dump_file))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        _exit_backup_failure("pg_dump_failed", result.stderr, "pg_dump")
    logger.info("pg_dump_done", file=str(dump_file))


def _workspace_tar_sources(workspace: Path) -> list[str]:
    sources: list[str] = []
    if (workspace / "memory").is_dir():
        sources.append("memory")
    if (workspace / "MEMORY.md").is_file():
        sources.append("MEMORY.md")
    return sources


def _run_workspace_archive_step(
    workspace: Path,
    archive_file: Path,
) -> Path | None:
    tar_sources = _workspace_tar_sources(workspace)
    if not tar_sources:
        logger.warning("no_workspace_memory_files")
        return None

    tar_cmd = ["tar", "czf", str(archive_file), "-C", str(workspace), *tar_sources]
    logger.info("tar_start", sources=tar_sources, output=str(archive_file))
    result = subprocess.run(tar_cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        _exit_backup_failure("tar_failed", result.stderr, "tar")
    logger.info("tar_done", file=str(archive_file))
    return archive_file


def _write_manifest(
    manifest_file: Path,
    timestamp: str,
    artifacts: list[Path | None],
) -> None:
    lines = ["# NeoMAGI Backup Manifest", f"# Created: {timestamp} UTC", ""]
    for artifact in artifacts:
        if artifact and artifact.exists():
            lines.append(f"{_sha256(artifact)}  {artifact.name}")
    manifest_file.write_text("\n".join(lines) + "\n")


def _print_backup_summary(
    output_dir: Path,
    dump_file: Path,
    archive_file: Path | None,
    manifest_file: Path,
) -> None:
    print(f"Backup complete → {output_dir}")  # noqa: T201
    print(f"  DB dump:    {dump_file.name}")  # noqa: T201
    if archive_file:
        print(f"  Workspace:  {archive_file.name}")  # noqa: T201
    print(f"  Manifest:   {manifest_file.name}")  # noqa: T201


def run_backup(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")

    pg_dump = _check_pg_dump()
    dsn = _get_dsn()
    workspace = _assert_workspace_path_consistency()

    dump_file = output_dir / f"neomagi_{timestamp}.dump"
    _run_pg_dump_step(pg_dump, dsn, dump_file)

    archive_file = output_dir / f"workspace_memory_{timestamp}.tar.gz"
    archive_file = _run_workspace_archive_step(workspace, archive_file)

    manifest_file = output_dir / f"manifest_{timestamp}.txt"
    _write_manifest(manifest_file, timestamp, [dump_file, archive_file])
    _print_backup_summary(output_dir, dump_file, archive_file, manifest_file)


def main() -> None:
    setup_logging(json_output=False)
    parser = argparse.ArgumentParser(description="NeoMAGI backup — truth-source data export")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("backups"),
        help="Directory for backup output (default: ./backups)",
    )
    args = parser.parse_args()
    run_backup(args.output_dir)


if __name__ == "__main__":
    main()
