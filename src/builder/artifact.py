"""Artifact generation, rendering, and persistence for builder work memory.

Responsibilities:
- generate_artifact_id(): stable ID for artifact records
- render_artifact_markdown(): BuilderTaskRecord -> markdown text
- write_artifact(): persist markdown to workspace/artifacts/

All I/O is async (aiofiles). No PostgreSQL dependency (ADR 0055).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import aiofiles
import structlog

from src.builder.types import BuilderTaskRecord

logger = structlog.get_logger(__name__)


def generate_artifact_id() -> str:
    """Generate a stable artifact ID.

    TODO: Upgrade to UUIDv7 for time-ordered IDs when a suitable
    implementation is available. Using uuid4 as V1 fallback to avoid
    introducing a new dependency solely for this (ADR 0055 / ADR 0057).
    """
    return str(uuid.uuid4())


def render_artifact_markdown(record: BuilderTaskRecord) -> str:
    """Render a BuilderTaskRecord as a markdown artifact document."""
    lines: list[str] = []
    lines.append(f"# Builder Task: {record.task_brief}")
    lines.append("")
    lines.append(f"- **artifact_id**: `{record.artifact_id}`")
    if record.bead_id:
        lines.append(f"- **bead_id**: `{record.bead_id}`")
    lines.append(f"- **scope**: {record.scope}")
    lines.append("")

    if record.decision_snapshots:
        lines.append("## Decision Snapshots")
        lines.append("")
        for snap in record.decision_snapshots:
            lines.append(f"- {snap}")
        lines.append("")

    if record.todo_items:
        lines.append("## TODO Items")
        lines.append("")
        for item in record.todo_items:
            lines.append(f"- [ ] {item}")
        lines.append("")

    if record.blockers:
        lines.append("## Blockers")
        lines.append("")
        for blocker in record.blockers:
            lines.append(f"- {blocker}")
        lines.append("")

    if record.artifact_refs:
        lines.append("## Artifact References")
        lines.append("")
        for ref in record.artifact_refs:
            lines.append(f"- {ref}")
        lines.append("")

    if record.validation_summary:
        lines.append("## Validation Summary")
        lines.append("")
        lines.append(record.validation_summary)
        lines.append("")

    if record.promote_candidates:
        lines.append("## Promote Candidates")
        lines.append("")
        for candidate in record.promote_candidates:
            lines.append(f"- {candidate}")
        lines.append("")

    if record.next_recommended_action:
        lines.append("## Next Recommended Action")
        lines.append("")
        lines.append(record.next_recommended_action)
        lines.append("")

    return "\n".join(lines)


async def write_artifact(record: BuilderTaskRecord, base_dir: Path) -> Path:
    """Write a BuilderTaskRecord as a markdown file under *base_dir*.

    The file is placed at ``<base_dir>/builder_runs/<artifact_id>.md``.
    Parent directories are created if needed.

    Returns the path to the written file.
    """
    target_dir = base_dir / "builder_runs"
    target_dir.mkdir(parents=True, exist_ok=True)

    file_path = target_dir / f"{record.artifact_id}.md"
    content = render_artifact_markdown(record)

    async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
        await f.write(content)

    logger.info(
        "artifact_written",
        artifact_id=record.artifact_id,
        path=str(file_path),
    )
    return file_path
