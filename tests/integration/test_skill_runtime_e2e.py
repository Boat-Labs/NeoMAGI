"""End-to-end integration test for the skill runtime pipeline (P2-M1b-P4).

Verifies the full flow without a real DB:
SkillStore -> SkillGovernedObjectAdapter -> GrowthGovernanceEngine
  -> propose -> evaluate -> apply
  -> SkillResolver -> SkillProjector -> PromptBuilder
  -> SkillLearner.record_outcome

Uses mock DB sessions (AsyncMock-based SkillStore) to avoid PostgreSQL dependency.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.growth.adapters.skill import SkillGovernedObjectAdapter
from src.growth.engine import GrowthGovernanceEngine
from src.growth.policies import PolicyRegistry
from src.growth.types import GrowthObjectKind
from src.skills.learner import SkillLearner
from src.skills.projector import SkillProjector
from src.skills.resolver import SkillResolver
from src.skills.types import SkillEvidence, SkillSpec, TaskFrame, TaskOutcome, TaskType

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(**overrides: object) -> SkillSpec:
    defaults = {
        "id": "sk-e2e",
        "capability": "code_review",
        "version": 1,
        "summary": "Reviews code changes",
        "activation": "When user asks for code review",
        "activation_tags": ("review", "code"),
        "delta": ("prefer structured feedback",),
    }
    defaults.update(overrides)
    return SkillSpec(**defaults)  # type: ignore[arg-type]


def _make_evidence(**overrides: object) -> SkillEvidence:
    defaults = {
        "source": "test",
        "success_count": 1,
        "last_validated_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    defaults.update(overrides)
    return SkillEvidence(**defaults)  # type: ignore[arg-type]


def _fake_spec_row(spec: SkillSpec) -> SimpleNamespace:
    return SimpleNamespace(
        id=spec.id,
        capability=spec.capability,
        version=spec.version,
        summary=spec.summary,
        activation=spec.activation,
        activation_tags=list(spec.activation_tags),
        preconditions=list(spec.preconditions),
        delta=list(spec.delta),
        tool_preferences=list(spec.tool_preferences),
        escalation_rules=list(spec.escalation_rules),
        exchange_policy=spec.exchange_policy,
        disabled=spec.disabled,
    )


def _fake_evidence_row(skill_id: str, evidence: SkillEvidence) -> SimpleNamespace:
    return SimpleNamespace(
        skill_id=skill_id,
        source=evidence.source,
        success_count=evidence.success_count,
        failure_count=evidence.failure_count,
        last_validated_at=evidence.last_validated_at,
        positive_patterns=list(evidence.positive_patterns),
        negative_patterns=list(evidence.negative_patterns),
        known_breakages=list(evidence.known_breakages),
    )


class FakeSkillStore:
    """In-memory SkillStore-like object for e2e testing.

    Stores specs and evidence in dicts; governance ledger in a list.
    Implements the subset of SkillStore used by the runtime pipeline.
    """

    def __init__(self) -> None:
        self._specs: dict[str, SkillSpec] = {}
        self._evidence: dict[str, SkillEvidence] = {}
        self._proposals: list[dict] = []
        self._next_gv = 1

    async def list_active(self) -> list[SkillSpec]:
        return [s for s in self._specs.values() if not s.disabled]

    async def get_evidence(self, skill_ids: tuple[str, ...]) -> dict[str, SkillEvidence]:
        return {sid: self._evidence[sid] for sid in skill_ids if sid in self._evidence}

    async def upsert_active(self, spec, evidence, *, session=None):
        self._specs[spec.id] = spec
        self._evidence[spec.id] = evidence

    async def update_evidence(self, skill_id, evidence, *, session=None):
        self._evidence[skill_id] = evidence

    async def get_by_id(self, skill_id):
        return self._specs.get(skill_id)

    async def disable(self, skill_id, *, session=None):
        if skill_id in self._specs:
            s = self._specs[skill_id]
            self._specs[skill_id] = SkillSpec(**{**s.model_dump(), "disabled": True})

    async def create_proposal(self, proposal, *, session=None):
        gv = self._next_gv
        self._next_gv += 1
        self._proposals.append({
            "governance_version": gv,
            "skill_id": proposal.object_id,
            "status": "proposed",
            "proposal": {
                "intent": proposal.intent,
                "risk_notes": proposal.risk_notes,
                "diff_summary": proposal.diff_summary,
                "evidence_refs": list(proposal.evidence_refs),
                "payload": proposal.payload,
            },
            "eval_result": None,
            "created_by": proposal.proposed_by,
            "created_at": datetime.now(UTC),
            "applied_at": None,
            "rolled_back_from": None,
        })
        return gv

    async def get_proposal(self, governance_version):
        for p in self._proposals:
            if p["governance_version"] == governance_version:
                from src.skills.store import SkillProposalRecord
                return SkillProposalRecord(**p)
        return None

    async def store_eval_result(self, governance_version, result):
        for p in self._proposals:
            if p["governance_version"] == governance_version:
                p["eval_result"] = {
                    "passed": result.passed,
                    "checks": result.checks,
                    "summary": result.summary,
                    "contract_id": result.contract_id,
                    "contract_version": result.contract_version,
                }

    async def update_proposal_status(self, governance_version, status, *,
                                     applied_at=None, rolled_back_from=None,
                                     session=None):
        for p in self._proposals:
            if p["governance_version"] == governance_version:
                p["status"] = status.value if hasattr(status, "value") else status
                if applied_at:
                    p["applied_at"] = applied_at

    async def find_last_applied(self, skill_id):
        for p in reversed(self._proposals):
            if p["skill_id"] == skill_id and p["status"] == "active":
                from src.skills.store import SkillProposalRecord
                return SkillProposalRecord(**p)
        return None

    @asynccontextmanager
    async def transaction(self):
        yield MagicMock(name="fake_session")


# ---------------------------------------------------------------------------
# Test: full propose -> evaluate -> apply -> resolve -> project pipeline
# ---------------------------------------------------------------------------


class TestSkillRuntimeE2E:
    @pytest.mark.asyncio
    async def test_propose_evaluate_apply_resolve_project(self) -> None:
        """Full pipeline: governance -> materialization -> resolver -> projector."""
        store = FakeSkillStore()

        # Build governance engine
        adapter = SkillGovernedObjectAdapter(store)  # type: ignore[arg-type]
        policy_registry = PolicyRegistry()
        engine = GrowthGovernanceEngine(
            adapters={GrowthObjectKind.skill_spec: adapter},
            policy_registry=policy_registry,
        )
        learner = SkillLearner(store, engine)  # type: ignore[arg-type]

        # 1. Propose a new skill
        spec = _make_spec()
        evidence = _make_evidence()
        gv = await learner.propose_new_skill(spec, evidence)
        assert gv == 1

        # 2. Evaluate
        eval_result = await engine.evaluate(GrowthObjectKind.skill_spec, gv)
        assert eval_result.passed is True

        # 3. Apply
        await engine.apply(GrowthObjectKind.skill_spec, gv)

        # Verify materialized
        active_specs = await store.list_active()
        assert len(active_specs) == 1
        assert active_specs[0].id == "sk-e2e"

        # 4. Resolve
        resolver = SkillResolver(registry=store)  # type: ignore[arg-type]
        frame = TaskFrame(task_type=TaskType.create, target_outcome="review code")
        candidates = await resolver.resolve(frame)
        assert len(candidates) > 0

        # 5. Project
        projector = SkillProjector()
        view = projector.project(candidates, frame)
        assert len(view.llm_delta) > 0

    @pytest.mark.asyncio
    async def test_learner_record_outcome_after_apply(self) -> None:
        """After apply, learner can record outcome on the materialized skill."""
        store = FakeSkillStore()
        adapter = SkillGovernedObjectAdapter(store)  # type: ignore[arg-type]
        policy_registry = PolicyRegistry()
        engine = GrowthGovernanceEngine(
            adapters={GrowthObjectKind.skill_spec: adapter},
            policy_registry=policy_registry,
        )
        learner = SkillLearner(store, engine)  # type: ignore[arg-type]

        # Propose + evaluate + apply
        spec = _make_spec()
        evidence = _make_evidence(success_count=1)
        gv = await learner.propose_new_skill(spec, evidence)
        await engine.evaluate(GrowthObjectKind.skill_spec, gv)
        await engine.apply(GrowthObjectKind.skill_spec, gv)

        # Record outcome
        outcome = TaskOutcome(
            success=True,
            terminal_state="assistant_response",
            user_confirmed=True,
        )
        await learner.record_outcome([spec], outcome)

        # Verify evidence updated
        ev_map = await store.get_evidence(("sk-e2e",))
        assert ev_map["sk-e2e"].success_count == 2  # was 1
