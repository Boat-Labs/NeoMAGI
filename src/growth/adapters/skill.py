"""Skill governed-object adapter: connects skill_spec to the growth governance kernel.

Implements :class:`GovernedObjectAdapter` protocol for
:attr:`GrowthObjectKind.skill_spec`.

Pins ``SKILL_SPEC_EVAL_CONTRACT_V1`` before every evaluation (ADR 0054 section 1a).
All 5 eval checks are deterministic — no LLM calls, no network access.
``apply()`` and ``rollback()`` execute in single DB transactions (atomic).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

from src.growth.contracts import SKILL_SPEC_EVAL_CONTRACT_V1
from src.growth.types import (
    GrowthEvalResult,
    GrowthLifecycleStatus,
    GrowthObjectKind,
    GrowthProposal,
)
from src.skills.types import SkillEvidence, SkillSpec

if TYPE_CHECKING:
    from src.skills.store import SkillStore

logger = structlog.get_logger()

# ---------------------------------------------------------------------------
# V1 static blocklist for projection safety
# ---------------------------------------------------------------------------

_PROJECTION_BLOCKLIST_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+previous", re.IGNORECASE),
    re.compile(r"system:", re.IGNORECASE),
    re.compile(r"<\|", re.IGNORECASE),
    re.compile(r"forget\s+(all|everything|your)\s+(instructions|rules)", re.IGNORECASE),
    re.compile(r"you\s+are\s+now", re.IGNORECASE),
    re.compile(r"disregard\s+(all|any|the)\s+(previous|above)", re.IGNORECASE),
)

_MAX_DELTA_PER_SKILL = 3


# ---------------------------------------------------------------------------
# Eval check helpers
# ---------------------------------------------------------------------------


def _check_schema_validity(spec: SkillSpec, evidence: SkillEvidence) -> dict:
    """Check 1: SkillSpec/SkillEvidence Pydantic validation; required fields non-empty."""
    errors: list[str] = []
    if not spec.id:
        errors.append("id is empty")
    if not spec.capability:
        errors.append("capability is empty")
    if not spec.summary:
        errors.append("summary is empty")
    if not spec.activation:
        errors.append("activation is empty")
    if spec.version < 1:
        errors.append(f"version must be >= 1, got {spec.version}")
    if not evidence.source:
        errors.append("evidence.source is empty")
    passed = len(errors) == 0
    return {
        "name": "schema_validity",
        "passed": passed,
        "detail": "Schema valid" if passed else "; ".join(errors),
    }


def _check_activation_correctness(spec: SkillSpec) -> dict:
    """Check 2: activation_tags normalised, non-empty, no duplicates; no static contradictions."""
    errors: list[str] = []
    tags = spec.activation_tags
    if not tags:
        errors.append("activation_tags is empty")
    else:
        normalised = tuple(t.strip().lower() for t in tags)
        if any(not t for t in normalised):
            errors.append("activation_tags contains empty tag after normalisation")
        if len(set(normalised)) != len(normalised):
            errors.append("activation_tags contains duplicates")
    # Static contradiction: preconditions that negate activation_tags
    tag_set = {t.strip().lower() for t in tags}
    for pre in spec.preconditions:
        normalised_pre = pre.strip().lower()
        if normalised_pre.startswith("not:"):
            negated = normalised_pre[4:].strip()
            if negated in tag_set:
                errors.append(f"precondition '{pre}' contradicts activation_tag '{negated}'")
    passed = len(errors) == 0
    return {
        "name": "activation_correctness",
        "passed": passed,
        "detail": "Activation correct" if passed else "; ".join(errors),
    }


def _check_projection_safety(spec: SkillSpec) -> dict:
    """Check 3: delta budget + V1 static blocklist (no semantic analysis)."""
    errors: list[str] = []
    if len(spec.delta) > _MAX_DELTA_PER_SKILL:
        errors.append(
            f"delta has {len(spec.delta)} entries, max {_MAX_DELTA_PER_SKILL}"
        )
    # Scan delta for prompt injection patterns
    for entry in spec.delta:
        for pattern in _PROJECTION_BLOCKLIST_PATTERNS:
            if pattern.search(entry):
                errors.append(f"delta entry blocked by pattern: {pattern.pattern!r}")
                break
    # Also scan activation and summary
    for field_name, field_val in [("activation", spec.activation), ("summary", spec.summary)]:
        for pattern in _PROJECTION_BLOCKLIST_PATTERNS:
            if pattern.search(field_val):
                errors.append(
                    f"{field_name} blocked by pattern: {pattern.pattern!r}"
                )
                break
    passed = len(errors) == 0
    return {
        "name": "projection_safety",
        "passed": passed,
        "detail": "Projection safe" if passed else "; ".join(errors),
    }


def _check_learning_discipline(evidence: SkillEvidence) -> dict:
    """Check 4: initial negative experience must come from deterministic provenance."""
    errors: list[str] = []
    # V1 rule: if failure_count > 0 and source is not deterministic, reject
    deterministic_sources = {"deterministic", "test", "eval", "manual", "system"}
    if evidence.failure_count > 0 and evidence.source not in deterministic_sources:
        errors.append(
            f"failure_count={evidence.failure_count} but source='{evidence.source}' "
            "is not a deterministic provenance"
        )
    if evidence.negative_patterns and evidence.source not in deterministic_sources:
        errors.append(
            f"negative_patterns present but source='{evidence.source}' "
            "is not a deterministic provenance"
        )
    passed = len(errors) == 0
    return {
        "name": "learning_discipline",
        "passed": passed,
        "detail": "Learning discipline OK" if passed else "; ".join(errors),
    }


def _check_scope_claim_consistency(spec: SkillSpec, evidence: SkillEvidence) -> dict:
    """Check 5: exchange_policy vs evidence strength."""
    errors: list[str] = []
    if spec.exchange_policy == "local_only":
        # Always passes for local_only
        pass
    elif spec.exchange_policy in ("reusable", "promotable"):
        # Require stronger evidence for shared scopes
        if evidence.success_count < 1:
            errors.append(
                f"exchange_policy='{spec.exchange_policy}' requires success_count >= 1"
            )
        if not evidence.last_validated_at:
            errors.append(
                f"exchange_policy='{spec.exchange_policy}' requires last_validated_at"
            )
    passed = len(errors) == 0
    return {
        "name": "scope_claim_consistency",
        "passed": passed,
        "detail": "Scope claim consistent" if passed else "; ".join(errors),
    }


# ---------------------------------------------------------------------------
# SkillGovernedObjectAdapter
# ---------------------------------------------------------------------------


class SkillGovernedObjectAdapter:
    """Adapter connecting skill_spec to the growth governance kernel.

    Implements :class:`GovernedObjectAdapter` protocol for
    :attr:`GrowthObjectKind.skill_spec`.
    """

    def __init__(self, store: SkillStore) -> None:
        self._store = store
        self._contract = SKILL_SPEC_EVAL_CONTRACT_V1

    @property
    def kind(self) -> GrowthObjectKind:
        return GrowthObjectKind.skill_spec

    async def propose(self, proposal: GrowthProposal) -> int:
        """Create a governance proposal from payload.

        Requires ``proposal.payload["skill_spec"]`` (dict) and
        ``proposal.payload["skill_evidence"]`` (dict).
        Returns the governance_version.
        """
        # Validate payload shape
        raw_spec = proposal.payload.get("skill_spec")
        raw_evidence = proposal.payload.get("skill_evidence")
        if not isinstance(raw_spec, dict):
            raise ValueError(
                "SkillGovernedObjectAdapter.propose() requires "
                'proposal.payload["skill_spec"] to be a dict'
            )
        if not isinstance(raw_evidence, dict):
            raise ValueError(
                "SkillGovernedObjectAdapter.propose() requires "
                'proposal.payload["skill_evidence"] to be a dict'
            )
        # Validate that they parse as domain objects (fail fast)
        SkillSpec(**raw_spec)
        SkillEvidence(**raw_evidence)

        gv = await self._store.create_proposal(proposal)
        logger.info("skill_adapter_proposed", governance_version=gv, skill_id=proposal.object_id)
        return gv

    async def evaluate(self, version: int) -> GrowthEvalResult:
        """Run 5 deterministic eval checks against a proposed skill spec.

        Pins ``SKILL_SPEC_EVAL_CONTRACT_V1`` (ADR 0054 section 1a).
        """
        contract = self._contract
        record = await self._store.get_proposal(version)
        if record is None:
            return GrowthEvalResult(
                passed=False,
                summary=f"Governance version {version} not found",
                contract_id=contract.contract_id,
                contract_version=contract.version,
            )
        if record.status != GrowthLifecycleStatus.proposed:
            return GrowthEvalResult(
                passed=False,
                summary=f"Version {version} is '{record.status}', not 'proposed'",
                contract_id=contract.contract_id,
                contract_version=contract.version,
            )

        # Extract spec + evidence from proposal payload
        payload = record.proposal.get("payload", {})
        raw_spec = payload.get("skill_spec", {})
        raw_evidence = payload.get("skill_evidence", {})
        try:
            spec = SkillSpec(**raw_spec)
            evidence = SkillEvidence(**raw_evidence)
        except Exception as exc:
            return GrowthEvalResult(
                passed=False,
                checks=[{
                    "name": "schema_validity",
                    "passed": False,
                    "detail": f"Payload parse error: {exc}",
                }],
                summary=f"Payload parse error: {exc}",
                contract_id=contract.contract_id,
                contract_version=contract.version,
            )

        # Run 5 deterministic checks
        checks = [
            _check_schema_validity(spec, evidence),
            _check_activation_correctness(spec),
            _check_projection_safety(spec),
            _check_learning_discipline(evidence),
            _check_scope_claim_consistency(spec, evidence),
        ]
        passed = all(c["passed"] for c in checks)
        summary = (
            "All checks passed"
            if passed
            else "Failed: " + ", ".join(c["name"] for c in checks if not c["passed"])
        )
        result = GrowthEvalResult(
            passed=passed,
            checks=checks,
            summary=summary,
            contract_id=contract.contract_id,
            contract_version=contract.version,
        )
        await self._store.store_eval_result(version, result)
        logger.info("skill_adapter_evaluated", governance_version=version, passed=passed)
        return result

    async def apply(self, version: int) -> None:
        """Materialize a passed proposal to skill_specs + skill_evidence (atomic).

        Three-step write in single transaction:
        1. Check eval_result.passed == True
        2. Upsert skill_specs + skill_evidence
        3. Mark ledger row as active

        Steps 2-3 execute within a single DB transaction so that a failure
        in either rolls back both (no current-state / ledger drift).
        """
        record = await self._store.get_proposal(version)
        if record is None:
            raise ValueError(f"Governance version {version} not found")
        if record.status != GrowthLifecycleStatus.proposed:
            raise ValueError(
                f"Cannot apply version {version}: status is '{record.status}'"
            )
        if not record.eval_result or not record.eval_result.get("passed"):
            raise ValueError(
                f"Cannot apply version {version}: eval not passed"
            )

        payload = record.proposal.get("payload", {})
        spec = SkillSpec(**payload["skill_spec"])
        evidence = SkillEvidence(**payload["skill_evidence"])

        # Atomic: upsert current-state + update ledger in one transaction
        async with self._store.transaction() as session:
            await self._store.upsert_active(spec, evidence, session=session)
            await self._store.update_proposal_status(
                version,
                GrowthLifecycleStatus.active,
                applied_at=datetime.now(UTC),
                session=session,
            )
        logger.info("skill_adapter_applied", governance_version=version, skill_id=spec.id)

    async def rollback(self, **kwargs: object) -> int:
        """Rollback to previous applied snapshot or disable.

        If a previous applied snapshot exists -> materialize as new active + rollback entry.
        If no recoverable snapshot -> disable current skill + rollback entry.
        Returns the governance_version of the rollback entry.

        All writes execute within a single DB transaction (atomic).
        """
        skill_id = kwargs.get("skill_id")
        if not isinstance(skill_id, str):
            raise ValueError("rollback() requires skill_id as keyword argument")

        # Find last applied version (read, outside the write transaction)
        last_applied = await self._store.find_last_applied(skill_id)

        async with self._store.transaction() as session:
            if last_applied is not None:
                # Re-materialize previous snapshot
                payload = last_applied.proposal.get("payload", {})
                spec = SkillSpec(**payload["skill_spec"])
                evidence = SkillEvidence(**payload["skill_evidence"])
                await self._store.upsert_active(spec, evidence, session=session)
                # Mark current active as rolled_back
                await self._store.update_proposal_status(
                    last_applied.governance_version,
                    GrowthLifecycleStatus.rolled_back,
                    session=session,
                )
            else:
                # No recoverable snapshot -> disable
                await self._store.disable(skill_id, session=session)

            # Create a rollback ledger entry
            rollback_proposal = GrowthProposal(
                object_kind=GrowthObjectKind.skill_spec,
                object_id=skill_id,
                intent="rollback",
                risk_notes="System rollback",
                diff_summary="Rollback to previous version or disable",
                proposed_by="system",
            )
            gv = await self._store.create_proposal(rollback_proposal, session=session)
            await self._store.update_proposal_status(
                gv,
                GrowthLifecycleStatus.rolled_back,
                rolled_back_from=(
                    last_applied.governance_version if last_applied else None
                ),
                session=session,
            )

        logger.info("skill_adapter_rolled_back", governance_version=gv, skill_id=skill_id)
        return gv

    async def veto(self, version: int) -> None:
        """Veto a proposed or active governance version.

        Unapplied proposal -> mark vetoed.
        Active -> rollback/disable path.
        """
        record = await self._store.get_proposal(version)
        if record is None:
            raise ValueError(f"Governance version {version} not found")

        if record.status == GrowthLifecycleStatus.proposed:
            await self._store.update_proposal_status(version, GrowthLifecycleStatus.vetoed)
            logger.info("skill_adapter_vetoed", governance_version=version, was_status="proposed")
        elif record.status == GrowthLifecycleStatus.active:
            await self.rollback(skill_id=record.skill_id)
            logger.info("skill_adapter_vetoed", governance_version=version, was_status="active")
        else:
            raise ValueError(
                f"Cannot veto version {version}: status is '{record.status}'"
            )

    async def get_active(self) -> list[SkillSpec]:
        """Return all active (non-disabled) skills. Collection semantics."""
        return await self._store.list_active()
