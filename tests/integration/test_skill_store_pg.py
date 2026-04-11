"""PostgreSQL integration tests for SkillStore JSONB persistence."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import DB_SCHEMA
from src.growth.types import GrowthEvalResult, GrowthObjectKind, GrowthProposal
from src.session.database import ensure_schema
from src.skills.store import SkillStore
from src.skills.types import SkillEvidence, SkillSpec


def _make_skill_spec(spec_id: str = "sk-jsonb-pg") -> SkillSpec:
    return SkillSpec(
        id=spec_id, capability="general", version=1,
        summary="Summarize the goal, give three steps, then report validation.",
        activation="Activate for taught general tasks",
        activation_tags=("general", "summary", "validation"),
        preconditions=("user_teaching_intent",),
        delta=("Summarize the user's goal first.", "List exactly three execution steps.",
               "End with verification results."),
        tool_preferences=("local",),
        escalation_rules=("ask_for_missing_context",),
    )


def _make_updated_evidence() -> SkillEvidence:
    return SkillEvidence(
        source="human-taught", success_count=1, failure_count=1,
        positive_patterns=("three_steps", "verification_tail"),
        negative_patterns=("missing_goal",),
        known_breakages=("ambiguous_scope",),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_store_persists_jsonb_fields_with_asyncpg(
    db_engine, db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Raw text() JSONB writes should work end-to-end with asyncpg."""
    await ensure_schema(db_engine, DB_SCHEMA)
    store = SkillStore(db_session_factory)

    spec = _make_skill_spec()
    evidence = SkillEvidence(
        source="human-taught", positive_patterns=("three_steps",),
        known_breakages=("missing_goal",),
    )
    await store.upsert_active(spec, evidence)

    stored_spec = await store.get_by_id(spec.id)
    assert stored_spec is not None
    assert stored_spec.activation_tags == spec.activation_tags
    assert stored_spec.delta == spec.delta

    updated_evidence = _make_updated_evidence()
    await store.update_evidence(spec.id, updated_evidence)
    evidence_map = await store.get_evidence((spec.id,))
    assert evidence_map[spec.id].positive_patterns == updated_evidence.positive_patterns
    assert evidence_map[spec.id].negative_patterns == updated_evidence.negative_patterns


@pytest.mark.integration
@pytest.mark.asyncio
async def test_skill_store_governance_jsonb_with_asyncpg(
    db_engine, db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Governance proposal + eval result JSONB round-trip."""
    await ensure_schema(db_engine, DB_SCHEMA)
    store = SkillStore(db_session_factory)

    spec = _make_skill_spec("sk-jsonb-gov")
    updated_evidence = _make_updated_evidence()
    await store.upsert_active(spec, updated_evidence)

    proposal = GrowthProposal(
        object_kind=GrowthObjectKind.skill_spec, object_id=spec.id,
        intent="Create skill: general",
        risk_notes="New skill proposal via SkillLearner",
        diff_summary="记住这个方法：以后这类任务，先总结目标，再列出 3 条执行步骤，最后给验证结果。",
        payload={"skill_spec": spec.model_dump(), "skill_evidence": updated_evidence.model_dump()},
        proposed_by="user",
    )
    governance_version = await store.create_proposal(proposal)
    record = await store.get_proposal(governance_version)
    assert record is not None
    assert record.created_by == "user"
    assert record.proposal["payload"]["skill_spec"]["activation_tags"] == list(spec.activation_tags)

    eval_result = GrowthEvalResult(
        passed=True,
        checks=[{"name": "schema_validity", "passed": True, "detail": "ok"}],
        summary="All checks passed", contract_id="skill_spec_v1", contract_version=1,
    )
    await store.store_eval_result(governance_version, eval_result)
    evaluated = await store.get_proposal(governance_version)
    assert evaluated is not None
    assert evaluated.eval_result is not None
    assert evaluated.eval_result["passed"] is True
