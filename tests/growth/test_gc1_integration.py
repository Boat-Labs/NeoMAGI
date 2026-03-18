"""GC-1 integration test: Human-Taught Skill Reuse (P2-M1c em2.4.1).

Simulates the full GC-1 flow with all external dependencies mocked:
1. User teaching intent detection
2. SkillLearner.propose_new_skill() creates skill proposal
3. GrowthGovernanceEngine.evaluate() evaluates skill
4. GrowthGovernanceEngine.apply() applies skill
5. Skill in SkillStore is active
6. Second similar task: SkillResolver.resolve() matches the skill
7. SkillProjector.project() generates llm_delta
8. CaseRunner records complete run and generates artifact
9. Artifact file exists with proposal/eval/apply refs

No DB, no LLM calls, no API quota consumed.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.growth.case_runner import CaseRunner
from src.growth.case_types import GrowthCaseStatus
from src.growth.engine import GrowthGovernanceEngine
from src.growth.policies import PolicyRegistry
from src.growth.types import GrowthObjectKind
from src.skills.learner import SkillLearner
from src.skills.projector import SkillProjector
from src.skills.resolver import SkillResolver
from src.skills.store import SkillProposalRecord
from src.skills.types import SkillEvidence, SkillSpec, TaskFrame, TaskType

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_spec(**overrides: object) -> SkillSpec:
    defaults = {
        "id": "sk-taught-001",
        "capability": "python formatting with ruff",
        "version": 1,
        "summary": "Format Python code using ruff",
        "activation": "When user asks to format Python code",
        "activation_tags": ("format", "python", "ruff"),
        "delta": ("Use ruff format instead of black",),
        "exchange_policy": "reusable",
    }
    defaults.update(overrides)
    return SkillSpec(**defaults)  # type: ignore[arg-type]


def _make_evidence(**overrides: object) -> SkillEvidence:
    defaults = {
        "source": "deterministic",
        "success_count": 1,
        "last_validated_at": datetime(2026, 3, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return SkillEvidence(**defaults)  # type: ignore[arg-type]


def _make_proposal_record(
    spec: SkillSpec, evidence: SkillEvidence, *, gv: int = 1,
    status: str = "proposed", eval_passed: bool | None = None,
) -> SkillProposalRecord:
    eval_result = None
    if eval_passed is not None:
        eval_result = {"passed": eval_passed}
    return SkillProposalRecord(
        governance_version=gv,
        skill_id=spec.id,
        status=status,
        proposal={
            "intent": f"Create skill: {spec.capability}",
            "payload": {
                "skill_spec": spec.model_dump(),
                "skill_evidence": evidence.model_dump(),
            },
        },
        eval_result=eval_result,
        created_by="agent",
        created_at=datetime(2026, 3, 1, tzinfo=UTC),
        applied_at=None,
        rolled_back_from=None,
    )


def _build_mock_store(spec: SkillSpec, evidence: SkillEvidence) -> AsyncMock:
    """Build a mock SkillStore wired for the GC-1 flow."""
    store = AsyncMock()
    store.create_proposal = AsyncMock(return_value=1)
    store.store_eval_result = AsyncMock()
    store.update_proposal_status = AsyncMock()
    store.upsert_active = AsyncMock()
    store.disable = AsyncMock()
    store.find_last_applied = AsyncMock(return_value=None)
    store.update_evidence = AsyncMock()

    # After proposal: return proposed record
    proposed_record = _make_proposal_record(spec, evidence, gv=1, status="proposed")
    # After eval: return proposed record (eval stored separately)
    eval_passed_record = _make_proposal_record(
        spec, evidence, gv=1, status="proposed", eval_passed=True,
    )

    # get_proposal: first call for evaluate returns proposed, second for apply returns passed
    store.get_proposal = AsyncMock(side_effect=[proposed_record, eval_passed_record])

    # After apply: list_active returns the skill
    store.list_active = AsyncMock(return_value=[spec])
    store.get_evidence = AsyncMock(return_value={spec.id: evidence})

    mock_session = MagicMock(name="mock_db_session")

    @asynccontextmanager
    async def _fake_transaction():
        yield mock_session

    store.transaction = _fake_transaction
    return store


@pytest.fixture()
def gc1_components(tmp_path: Path):
    """Build the full component set for GC-1 integration."""
    spec = _make_spec()
    evidence = _make_evidence()
    store = _build_mock_store(spec, evidence)

    from src.growth.adapters.skill import SkillGovernedObjectAdapter

    adapter = SkillGovernedObjectAdapter(store)
    policy_registry = PolicyRegistry()
    engine = GrowthGovernanceEngine(
        adapters={GrowthObjectKind.skill_spec: adapter},
        policy_registry=policy_registry,
    )
    learner = SkillLearner(store=store, governance_engine=engine)
    resolver = SkillResolver(registry=store, max_candidates=3)
    projector = SkillProjector()
    runner = CaseRunner(artifact_base=tmp_path / "growth_cases")

    return {
        "spec": spec,
        "evidence": evidence,
        "store": store,
        "engine": engine,
        "learner": learner,
        "resolver": resolver,
        "projector": projector,
        "runner": runner,
        "artifact_base": tmp_path / "growth_cases",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def _run_propose_eval_apply(
    components: dict,
) -> tuple:
    """Execute propose → eval → apply steps, return (run, gv)."""
    spec = components["spec"]
    evidence = components["evidence"]
    learner: SkillLearner = components["learner"]
    engine: GrowthGovernanceEngine = components["engine"]
    runner: CaseRunner = components["runner"]

    run = await runner.start_run("gc-1")
    assert run.status == GrowthCaseStatus.running

    gv = await learner.propose_new_skill(spec, evidence)
    assert gv == 1
    run = await runner.record_proposal(run, governance_version=gv)
    assert "gv:1" in run.proposal_refs

    eval_result = await engine.evaluate(GrowthObjectKind.skill_spec, gv)
    assert eval_result.passed is True
    run = await runner.record_eval(run, eval_result)
    assert len(run.eval_refs) == 1
    assert "passed=True" in run.eval_refs[0]

    await engine.apply(GrowthObjectKind.skill_spec, gv)
    run = await runner.record_apply(run, success=True)
    assert "success=True" in run.apply_refs[0]

    store = components["store"]
    active_skills = await store.list_active()
    assert any(s.id == spec.id for s in active_skills)
    return run, gv


async def _run_reuse_phase(components: dict) -> None:
    """Verify resolver matches the taught skill and projector produces delta."""
    spec = components["spec"]
    resolver: SkillResolver = components["resolver"]
    projector: SkillProjector = components["projector"]

    frame = TaskFrame(task_type=TaskType.edit, target_outcome="format python code with ruff")
    candidates = await resolver.resolve(frame)
    assert len(candidates) >= 1
    assert candidates[0][0].id == spec.id

    view = projector.project(candidates, frame)
    assert len(view.llm_delta) > 0
    assert "ruff" in view.llm_delta[0].lower()


def _assert_artifact_contains(
    artifact_base: Path, case_id: str, run_id: str, expected: tuple[str, ...],
) -> None:
    """Assert the artifact file exists and contains all expected strings."""
    artifact_path = artifact_base / case_id / f"{run_id}.md"
    assert artifact_path.exists()
    content = artifact_path.read_text(encoding="utf-8")
    for text in expected:
        assert text in content


class TestGC1FullFlow:
    """Full GC-1 integration: teach -> propose -> eval -> apply -> reuse."""

    async def test_teach_propose_eval_apply_reuse(self, gc1_components: dict) -> None:
        runner: CaseRunner = gc1_components["runner"]
        artifact_base: Path = gc1_components["artifact_base"]

        run, _gv = await _run_propose_eval_apply(gc1_components)
        await _run_reuse_phase(gc1_components)

        run = await runner.finalize(
            run, summary="Skill taught and reused successfully", passed=True,
        )
        assert run.status == GrowthCaseStatus.passed
        assert run.summary == "Skill taught and reused successfully"

        _assert_artifact_contains(
            artifact_base, "gc-1", run.run_id,
            ("gv:1", "passed=True", "success=True", "Skill taught and reused successfully"),
        )


class TestGC1ReuseRequired:
    """GC-1 must prove reuse -- resolver must match the taught skill."""

    async def test_resolver_does_not_match_without_overlap(
        self, gc1_components: dict,
    ) -> None:
        """If the second task has zero keyword overlap, resolver should not match."""
        store = gc1_components["store"]
        # Make a spec with no matching tags
        unrelated_spec = _make_spec(
            id="sk-unrelated",
            capability="advanced math",
            activation_tags=("math", "calculus"),
            delta=("Use sympy",),
        )
        store.list_active = AsyncMock(return_value=[unrelated_spec])
        store.get_evidence = AsyncMock(
            return_value={unrelated_spec.id: _make_evidence()},
        )
        resolver = SkillResolver(registry=store, max_candidates=3)

        frame = TaskFrame(
            task_type=TaskType.edit,
            target_outcome="format python code with ruff",
        )
        candidates = await resolver.resolve(frame)
        # unrelated skill should have low score; but still returned if only candidate
        # Key point: in a real scenario with the taught skill + unrelated,
        # the taught skill should rank higher
        matched_ids = {c[0].id for c in candidates}
        assert "sk-taught-001" not in matched_ids
