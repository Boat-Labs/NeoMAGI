"""Growth case runner: thin orchestration for growth case execution (P2-M1c).

CaseRunner is NOT a procedure runtime -- it tracks lifecycle steps
of a growth case run and persists artifact markdown to the workspace.
Each method returns an updated immutable GrowthCaseRun copy.

Artifact truth: workspace/artifacts/growth_cases/<case_id>/<run_id>.md
(ADR 0055 / ADR 0057).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import aiofiles
import structlog

from src.growth.case_types import GrowthCaseRun, GrowthCaseStatus
from src.growth.cases import get_case_spec
from src.growth.types import GrowthEvalResult

logger = structlog.get_logger()


def _render_run_markdown(run: GrowthCaseRun) -> str:
    """Render a GrowthCaseRun as a markdown artifact document."""
    spec = get_case_spec(run.case_id)
    title = spec.title if spec else run.case_id

    lines: list[str] = [f"# Growth Case Run: {title}", ""]
    lines.append(f"- **run_id**: `{run.run_id}`")
    lines.append(f"- **case_id**: `{run.case_id}`")
    lines.append(f"- **status**: `{run.status}`")
    lines.append("")

    _render_ref_section(lines, "Proposal Refs", run.proposal_refs)
    _render_ref_section(lines, "Eval Refs", run.eval_refs)
    _render_ref_section(lines, "Apply Refs", run.apply_refs)
    _render_ref_section(lines, "Rollback Refs", run.rollback_refs)
    _render_ref_section(lines, "Artifact Refs", run.artifact_refs)
    _render_ref_section(lines, "Linked Bead IDs", run.linked_bead_ids)

    if run.summary:
        lines += ["## Summary", "", run.summary, ""]

    return "\n".join(lines)


def _render_ref_section(
    lines: list[str], heading: str, refs: tuple[str, ...],
) -> None:
    """Append a markdown section with a bullet list if *refs* is non-empty."""
    if not refs:
        return
    lines += [f"## {heading}", ""]
    lines += [f"- {ref}" for ref in refs]
    lines.append("")


async def _write_artifact(run: GrowthCaseRun, base: Path) -> Path:
    """Persist run artifact markdown to the correct path."""
    target_dir = base / run.case_id
    target_dir.mkdir(parents=True, exist_ok=True)

    file_path = target_dir / f"{run.run_id}.md"
    content = _render_run_markdown(run)

    async with aiofiles.open(file_path, "w", encoding="utf-8") as f:
        await f.write(content)

    logger.info("case_run_artifact_written", run_id=run.run_id, path=str(file_path))
    return file_path


class CaseRunner:
    """Thin orchestration layer for growth case execution.

    Each method updates the run record and re-renders the artifact.
    """

    def __init__(self, artifact_base: Path) -> None:
        self._artifact_base = artifact_base

    async def start_run(self, case_id: str) -> GrowthCaseRun:
        """Start a new case run. Creates artifact file."""
        spec = get_case_spec(case_id)
        if spec is None:
            raise ValueError(f"Unknown growth case: {case_id}")

        run = GrowthCaseRun(
            run_id=str(uuid.uuid4()),
            case_id=case_id,
            status=GrowthCaseStatus.running,
        )
        await _write_artifact(run, self._artifact_base)
        logger.info("case_run_started", run_id=run.run_id, case_id=case_id)
        return run

    async def record_proposal(
        self, run: GrowthCaseRun, governance_version: int,
    ) -> GrowthCaseRun:
        """Record proposal ref in run."""
        updated = run.model_copy(
            update={"proposal_refs": (*run.proposal_refs, f"gv:{governance_version}")},
        )
        await _write_artifact(updated, self._artifact_base)
        return updated

    async def record_eval(
        self, run: GrowthCaseRun, eval_result: GrowthEvalResult,
    ) -> GrowthCaseRun:
        """Record eval result ref in run."""
        ref = f"eval:passed={eval_result.passed}"
        if eval_result.contract_id:
            ref += f",contract={eval_result.contract_id}"
        updated = run.model_copy(
            update={"eval_refs": (*run.eval_refs, ref)},
        )
        await _write_artifact(updated, self._artifact_base)
        return updated

    async def record_apply(
        self, run: GrowthCaseRun, success: bool,
    ) -> GrowthCaseRun:
        """Record apply result. If failed, mark as failed."""
        new_status = run.status if success else GrowthCaseStatus.failed
        updated = run.model_copy(
            update={
                "apply_refs": (*run.apply_refs, f"apply:success={success}"),
                "status": new_status,
            },
        )
        await _write_artifact(updated, self._artifact_base)
        return updated

    async def record_rollback(self, run: GrowthCaseRun) -> GrowthCaseRun:
        """Record rollback. Mark as rolled_back."""
        updated = run.model_copy(
            update={
                "rollback_refs": (*run.rollback_refs, "rollback:executed"),
                "status": GrowthCaseStatus.rolled_back,
            },
        )
        await _write_artifact(updated, self._artifact_base)
        return updated

    async def record_veto(self, run: GrowthCaseRun) -> GrowthCaseRun:
        """Record veto. Mark as vetoed (symmetric with record_rollback)."""
        updated = run.model_copy(
            update={"status": GrowthCaseStatus.vetoed},
        )
        await _write_artifact(updated, self._artifact_base)
        return updated

    async def finalize(
        self, run: GrowthCaseRun, summary: str, *, passed: bool = True,
    ) -> GrowthCaseRun:
        """Write final artifact and return completed run."""
        final_status = GrowthCaseStatus.passed if passed else run.status
        _terminal = (
            GrowthCaseStatus.failed,
            GrowthCaseStatus.rolled_back,
            GrowthCaseStatus.vetoed,
        )
        if run.status in _terminal:
            final_status = run.status

        updated = run.model_copy(
            update={"summary": summary, "status": final_status},
        )
        await _write_artifact(updated, self._artifact_base)
        logger.info(
            "case_run_finalized",
            run_id=updated.run_id,
            status=updated.status,
        )
        return updated
