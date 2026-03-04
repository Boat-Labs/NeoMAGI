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
    "neomagi.sessions",
    "neomagi.messages",
    "neomagi.soul_versions",
    "neomagi.budget_state",
    "neomagi.budget_reservations",
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


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def run_backup(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")

    pg_dump = _check_pg_dump()
    dsn = _get_dsn()

    # --- Step 1: pg_dump truth-source tables ---
    dump_file = output_dir / f"neomagi_{timestamp}.dump"
    table_args: list[str] = []
    for t in TRUTH_TABLES:
        table_args.extend(["--table", t])

    cmd = [pg_dump, *table_args, "--format=custom", "-f", str(dump_file), dsn]
    logger.info("pg_dump_start", tables=TRUTH_TABLES, output=str(dump_file))
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        logger.error("pg_dump_failed", stderr=result.stderr)
        print(f"ERROR: pg_dump failed:\n{result.stderr}", file=sys.stderr)  # noqa: T201
        sys.exit(1)
    logger.info("pg_dump_done", file=str(dump_file))

    # --- Step 2: tar workspace memory files ---
    workspace = Path("workspace")
    archive_file = output_dir / f"workspace_memory_{timestamp}.tar.gz"
    tar_sources: list[str] = []
    memory_dir = workspace / "memory"
    memory_md = workspace / "MEMORY.md"
    if memory_dir.is_dir():
        tar_sources.append(str(memory_dir))
    if memory_md.is_file():
        tar_sources.append(str(memory_md))

    if tar_sources:
        tar_cmd = ["tar", "czf", str(archive_file), *tar_sources]
        logger.info("tar_start", sources=tar_sources, output=str(archive_file))
        result = subprocess.run(tar_cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error("tar_failed", stderr=result.stderr)
            print(f"ERROR: tar failed:\n{result.stderr}", file=sys.stderr)  # noqa: T201
            sys.exit(1)
        logger.info("tar_done", file=str(archive_file))
    else:
        logger.warning("no_workspace_memory_files")
        archive_file = None

    # --- Step 3: Output manifest ---
    manifest_file = output_dir / f"manifest_{timestamp}.txt"
    lines = ["# NeoMAGI Backup Manifest", f"# Created: {timestamp} UTC", ""]
    for f in [dump_file, archive_file]:
        if f and f.exists():
            checksum = _sha256(f)
            lines.append(f"{checksum}  {f.name}")
    manifest_file.write_text("\n".join(lines) + "\n")

    print(f"Backup complete → {output_dir}")  # noqa: T201
    print(f"  DB dump:    {dump_file.name}")  # noqa: T201
    if archive_file:
        print(f"  Workspace:  {archive_file.name}")  # noqa: T201
    print(f"  Manifest:   {manifest_file.name}")  # noqa: T201


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
