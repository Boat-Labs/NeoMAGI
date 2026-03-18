"""Builder work memory lifecycle: create, update, link.

Orchestrates artifact persistence (workspace/artifacts/) and
best-effort bd issue indexing (ADR 0055 dual-layer model).

All I/O is async. bd CLI interactions are best-effort: if the CLI
is unavailable or a command fails, the function degrades gracefully
to artifact-only mode (ADR 0055 fallback).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import aiofiles
import structlog

from src.builder.artifact import (
    generate_artifact_id,
    render_artifact_markdown,
    write_artifact,
)
from src.builder.types import BuilderTaskRecord

logger = structlog.get_logger(__name__)


async def _run_bd_command(*args: str) -> tuple[bool, str]:
    """Run a bd CLI command and return (success, stdout).

    Best-effort: returns (False, stderr) on any failure.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "bd",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        if proc.returncode == 0:
            return True, stdout.decode("utf-8", errors="replace")
        return False, stderr.decode("utf-8", errors="replace")
    except (FileNotFoundError, TimeoutError, OSError) as exc:
        logger.warning("bd_command_failed", args=args, error=str(exc))
        return False, str(exc)


async def create_builder_task(
    task_brief: str,
    scope: str,
    base_dir: Path,
    *,
    decision_snapshots: tuple[str, ...] = (),
    todo_items: tuple[str, ...] = (),
    blockers: tuple[str, ...] = (),
    artifact_refs: tuple[str, ...] = (),
) -> BuilderTaskRecord:
    """Create a new builder task: artifact + optional bead index.

    1. Generates artifact_id
    2. Optionally creates a bd issue (best-effort)
    3. Writes canonical artifact to workspace/artifacts/
    4. Returns the immutable BuilderTaskRecord
    """
    artifact_id = generate_artifact_id()
    bead_id: str | None = None

    # Best-effort: create bd issue as index entry
    ok, output = await _run_bd_command(
        "create", task_brief, "--json",
    )
    if ok:
        try:
            data = json.loads(output)
            bead_id = data.get("id")
            logger.info("bead_created", bead_id=bead_id, artifact_id=artifact_id)
        except (json.JSONDecodeError, KeyError):
            logger.warning("bead_create_parse_failed", output=output[:200])

    record = BuilderTaskRecord(
        artifact_id=artifact_id,
        bead_id=bead_id,
        task_brief=task_brief,
        scope=scope,
        decision_snapshots=decision_snapshots,
        todo_items=todo_items,
        blockers=blockers,
        artifact_refs=artifact_refs,
    )

    await write_artifact(record, base_dir)

    # Best-effort: add artifact path as comment on bead
    if bead_id:
        artifact_path = base_dir / "builder_runs" / f"{artifact_id}.md"
        await _run_bd_command(
            "comments", "add", bead_id,
            f"artifact: {artifact_path}",
        )

    return record


async def update_task_progress(
    record: BuilderTaskRecord,
    base_dir: Path,
    *,
    decision_snapshots: tuple[str, ...] | None = None,
    todo_items: tuple[str, ...] | None = None,
    blockers: tuple[str, ...] | None = None,
    artifact_refs: tuple[str, ...] | None = None,
    validation_summary: str | None = None,
    promote_candidates: tuple[str, ...] | None = None,
    next_recommended_action: str | None = None,
) -> BuilderTaskRecord:
    """Update a builder task's progress: re-render artifact + optional bead comment.

    Creates a new immutable record (frozen model) with updated fields,
    overwrites the artifact file, and best-effort posts a progress
    comment to the linked bead.
    """
    updates: dict[str, object] = {}
    if decision_snapshots is not None:
        updates["decision_snapshots"] = decision_snapshots
    if todo_items is not None:
        updates["todo_items"] = todo_items
    if blockers is not None:
        updates["blockers"] = blockers
    if artifact_refs is not None:
        updates["artifact_refs"] = artifact_refs
    if validation_summary is not None:
        updates["validation_summary"] = validation_summary
    if promote_candidates is not None:
        updates["promote_candidates"] = promote_candidates
    if next_recommended_action is not None:
        updates["next_recommended_action"] = next_recommended_action

    updated = record.model_copy(update=updates)

    # Overwrite artifact file
    target_dir = base_dir / "builder_runs"
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / f"{updated.artifact_id}.md"
    content = render_artifact_markdown(updated)

    async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
        await f.write(content)

    logger.info(
        "artifact_updated",
        artifact_id=updated.artifact_id,
        fields=list(updates.keys()),
    )

    # Best-effort: post progress comment to bead
    if updated.bead_id and updates:
        summary = ", ".join(updates.keys())
        await _run_bd_command(
            "comments", "add", updated.bead_id,
            f"progress update: {summary}",
        )

    return updated


async def link_artifact_to_bead(bead_id: str, artifact_path: Path) -> None:
    """Add an artifact reference as a comment on an existing bead.

    Best-effort: logs a warning and returns on failure.
    """
    ok, output = await _run_bd_command(
        "comments", "add", bead_id,
        f"artifact: {artifact_path}",
    )
    if ok:
        logger.info("artifact_linked", bead_id=bead_id, path=str(artifact_path))
    else:
        logger.warning(
            "artifact_link_failed",
            bead_id=bead_id,
            path=str(artifact_path),
            error=output[:200],
        )
