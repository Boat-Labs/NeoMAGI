"""Tests for CaseRunner lifecycle operations (P2-M1c em2.4.3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.growth.case_runner import CaseRunner, _render_run_markdown
from src.growth.case_types import GrowthCaseRun, GrowthCaseStatus
from src.growth.types import GrowthEvalResult


@pytest.fixture()
def artifact_base(tmp_path: Path) -> Path:
    return tmp_path / "growth_cases"


@pytest.fixture()
def runner(artifact_base: Path) -> CaseRunner:
    return CaseRunner(artifact_base=artifact_base)


class TestRenderRunMarkdown:
    """_render_run_markdown should produce well-formed markdown."""

    def test_minimal_run(self) -> None:
        run = GrowthCaseRun(
            run_id="run-render-1",
            case_id="gc-1",
            status=GrowthCaseStatus.running,
        )
        md = _render_run_markdown(run)
        assert "# Growth Case Run:" in md
        assert "`run-render-1`" in md
        assert "`gc-1`" in md
        assert "`running`" in md
        # No optional sections
        assert "## Proposal Refs" not in md
        assert "## Summary" not in md

    def test_run_with_refs(self) -> None:
        run = GrowthCaseRun(
            run_id="run-render-2",
            case_id="gc-2",
            status=GrowthCaseStatus.passed,
            proposal_refs=("gv:1",),
            eval_refs=("eval:passed=True",),
            summary="All done",
        )
        md = _render_run_markdown(run)
        assert "## Proposal Refs" in md
        assert "- gv:1" in md
        assert "## Eval Refs" in md
        assert "## Summary" in md
        assert "All done" in md


class TestStartRun:
    """CaseRunner.start_run should create a run and artifact file."""

    async def test_creates_run(self, runner: CaseRunner) -> None:
        run = await runner.start_run("gc-1")
        assert run.case_id == "gc-1"
        assert run.status == GrowthCaseStatus.running
        assert len(run.run_id) > 0

    async def test_creates_artifact_file(
        self, runner: CaseRunner, artifact_base: Path,
    ) -> None:
        run = await runner.start_run("gc-1")
        path = artifact_base / "gc-1" / f"{run.run_id}.md"
        assert path.exists()
        content = path.read_text(encoding="utf-8")
        assert "# Growth Case Run:" in content
        assert run.run_id in content

    async def test_unknown_case_id_raises(self, runner: CaseRunner) -> None:
        with pytest.raises(ValueError, match="Unknown growth case"):
            await runner.start_run("gc-nonexistent")


class TestRecordProposal:
    """CaseRunner.record_proposal should append governance version ref."""

    async def test_appends_ref(self, runner: CaseRunner) -> None:
        run = await runner.start_run("gc-1")
        updated = await runner.record_proposal(run, governance_version=42)
        assert "gv:42" in updated.proposal_refs
        # Original unchanged
        assert run.proposal_refs == ()

    async def test_updates_artifact(
        self, runner: CaseRunner, artifact_base: Path,
    ) -> None:
        run = await runner.start_run("gc-1")
        updated = await runner.record_proposal(run, governance_version=7)
        path = artifact_base / "gc-1" / f"{updated.run_id}.md"
        content = path.read_text(encoding="utf-8")
        assert "gv:7" in content


class TestRecordEval:
    """CaseRunner.record_eval should append eval ref."""

    async def test_appends_eval_ref(self, runner: CaseRunner) -> None:
        run = await runner.start_run("gc-1")
        result = GrowthEvalResult(
            passed=True, summary="ok", contract_id="skill_spec_v1", contract_version=1,
        )
        updated = await runner.record_eval(run, result)
        assert len(updated.eval_refs) == 1
        assert "passed=True" in updated.eval_refs[0]
        assert "skill_spec_v1" in updated.eval_refs[0]

    async def test_failed_eval_ref(self, runner: CaseRunner) -> None:
        run = await runner.start_run("gc-2")
        result = GrowthEvalResult(passed=False, summary="fail")
        updated = await runner.record_eval(run, result)
        assert "passed=False" in updated.eval_refs[0]


class TestRecordApply:
    """CaseRunner.record_apply should record success/failure."""

    async def test_success_keeps_status(self, runner: CaseRunner) -> None:
        run = await runner.start_run("gc-1")
        updated = await runner.record_apply(run, success=True)
        assert updated.status == GrowthCaseStatus.running
        assert "success=True" in updated.apply_refs[0]

    async def test_failure_marks_failed(self, runner: CaseRunner) -> None:
        run = await runner.start_run("gc-1")
        updated = await runner.record_apply(run, success=False)
        assert updated.status == GrowthCaseStatus.failed
        assert "success=False" in updated.apply_refs[0]


class TestRecordRollback:
    """CaseRunner.record_rollback should mark as rolled_back."""

    async def test_marks_rolled_back(self, runner: CaseRunner) -> None:
        run = await runner.start_run("gc-2")
        updated = await runner.record_rollback(run)
        assert updated.status == GrowthCaseStatus.rolled_back
        assert "rollback:executed" in updated.rollback_refs


class TestRecordVeto:
    """CaseRunner.record_veto should mark as vetoed."""

    async def test_marks_vetoed(self, runner: CaseRunner) -> None:
        run = await runner.start_run("gc-2")
        updated = await runner.record_veto(run)
        assert updated.status == GrowthCaseStatus.vetoed


class TestFinalize:
    """CaseRunner.finalize should set summary and final status."""

    async def test_finalize_passed(self, runner: CaseRunner) -> None:
        run = await runner.start_run("gc-1")
        final = await runner.finalize(run, summary="All checks passed", passed=True)
        assert final.status == GrowthCaseStatus.passed
        assert final.summary == "All checks passed"

    async def test_finalize_preserves_failed_status(self, runner: CaseRunner) -> None:
        run = await runner.start_run("gc-1")
        failed = await runner.record_apply(run, success=False)
        final = await runner.finalize(failed, summary="Apply failed", passed=True)
        # Status should remain failed, not be overridden to passed
        assert final.status == GrowthCaseStatus.failed

    async def test_finalize_preserves_rolled_back_status(
        self, runner: CaseRunner,
    ) -> None:
        run = await runner.start_run("gc-2")
        rolled = await runner.record_rollback(run)
        final = await runner.finalize(rolled, summary="Rolled back")
        assert final.status == GrowthCaseStatus.rolled_back

    async def test_finalize_preserves_vetoed_status(self, runner: CaseRunner) -> None:
        run = await runner.start_run("gc-2")
        vetoed = await runner.record_veto(run)
        final = await runner.finalize(vetoed, summary="Eval veto", passed=False)
        assert final.status == GrowthCaseStatus.vetoed

    async def test_finalize_writes_artifact(
        self, runner: CaseRunner, artifact_base: Path,
    ) -> None:
        run = await runner.start_run("gc-1")
        final = await runner.finalize(run, summary="Done")
        path = artifact_base / "gc-1" / f"{final.run_id}.md"
        content = path.read_text(encoding="utf-8")
        assert "Done" in content
        assert "`passed`" in content
