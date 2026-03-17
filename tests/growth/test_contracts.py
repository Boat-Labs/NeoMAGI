"""Tests for growth eval contract profiles and registry.

Covers: GrowthEvalContract construction, immutability, PassRuleKind enum,
contract profile constants (soul, skill_spec, reserved skeletons),
get_contract() lookup, four-layer structure adherence, and contract pinning
in GrowthEvalResult.

No DB required.
"""

from __future__ import annotations

import pytest

from src.growth.contracts import (
    MEMORY_APP_SPEC_EVAL_CONTRACT_SKELETON,
    PROCEDURE_SPEC_EVAL_CONTRACT_SKELETON,
    SKILL_SPEC_EVAL_CONTRACT_V1,
    SOUL_EVAL_CONTRACT_V1,
    WRAPPER_TOOL_EVAL_CONTRACT_SKELETON,
    get_contract,
)
from src.growth.types import (
    GrowthEvalContract,
    GrowthEvalResult,
    GrowthObjectKind,
    PassRuleKind,
)


class TestPassRuleKind:
    def test_all_required_value(self) -> None:
        assert PassRuleKind.all_required == "all_required"

    def test_hard_pass_and_threshold_value(self) -> None:
        assert PassRuleKind.hard_pass_and_threshold == "hard_pass_and_threshold"

    def test_exactly_two_members(self) -> None:
        assert len(PassRuleKind) == 2


class TestGrowthEvalContractConstruction:
    def test_frozen(self) -> None:
        contract = SOUL_EVAL_CONTRACT_V1
        with pytest.raises(AttributeError):
            contract.version = 99  # type: ignore[misc]

    def test_all_fields_present(self) -> None:
        contract = SOUL_EVAL_CONTRACT_V1
        assert contract.contract_id == "soul_v1"
        assert contract.object_kind == GrowthObjectKind.soul
        assert contract.version == 1
        assert isinstance(contract.mutable_surface, tuple)
        assert isinstance(contract.immutable_harness, tuple)
        assert isinstance(contract.required_checks, tuple)
        assert isinstance(contract.required_artifacts, tuple)
        assert isinstance(contract.pass_rule_kind, PassRuleKind)
        assert isinstance(contract.pass_rule_params, tuple)
        assert isinstance(contract.veto_conditions, tuple)
        assert isinstance(contract.rollback_preconditions, tuple)
        assert isinstance(contract.budget_limits, tuple)


class TestSoulContractProfile:
    def test_kind_is_soul(self) -> None:
        assert SOUL_EVAL_CONTRACT_V1.object_kind == GrowthObjectKind.soul

    def test_pass_rule_all_required(self) -> None:
        assert SOUL_EVAL_CONTRACT_V1.pass_rule_kind == PassRuleKind.all_required

    def test_has_boundary_gate_checks(self) -> None:
        checks = SOUL_EVAL_CONTRACT_V1.required_checks
        # Names MUST match EvolutionEngine._build_eval_checks output
        assert "content_coherence" in checks  # includes non-empty
        assert "size_limit" in checks
        assert "diff_sanity" in checks
        assert len(checks) == 3

    def test_has_required_artifacts(self) -> None:
        arts = SOUL_EVAL_CONTRACT_V1.required_artifacts
        assert "intent" in arts
        assert "risk_notes" in arts
        assert "diff_summary" in arts
        assert "evidence_refs" in arts

    def test_has_veto_conditions(self) -> None:
        assert len(SOUL_EVAL_CONTRACT_V1.veto_conditions) > 0

    def test_has_rollback_preconditions(self) -> None:
        assert len(SOUL_EVAL_CONTRACT_V1.rollback_preconditions) > 0

    def test_mutable_surface_non_empty(self) -> None:
        assert len(SOUL_EVAL_CONTRACT_V1.mutable_surface) > 0

    def test_immutable_harness_non_empty(self) -> None:
        assert len(SOUL_EVAL_CONTRACT_V1.immutable_harness) > 0


class TestSkillSpecContractProfile:
    def test_kind_is_skill_spec(self) -> None:
        assert SKILL_SPEC_EVAL_CONTRACT_V1.object_kind == GrowthObjectKind.skill_spec

    def test_pass_rule_all_required(self) -> None:
        assert SKILL_SPEC_EVAL_CONTRACT_V1.pass_rule_kind == PassRuleKind.all_required

    def test_has_four_layer_checks(self) -> None:
        checks = SKILL_SPEC_EVAL_CONTRACT_V1.required_checks
        # Boundary gates
        assert "schema_validity" in checks
        assert "activation_correctness" in checks
        assert "projection_safety" in checks
        # Effect evidence
        assert "learning_discipline" in checks
        # Scope claim
        assert "scope_claim_consistency" in checks

    def test_has_budget_limits(self) -> None:
        assert len(SKILL_SPEC_EVAL_CONTRACT_V1.budget_limits) > 0

    def test_veto_includes_prompt_injection_risk(self) -> None:
        assert "prompt_injection_risk" in SKILL_SPEC_EVAL_CONTRACT_V1.veto_conditions

    def test_required_artifacts_include_skill_payload(self) -> None:
        arts = SKILL_SPEC_EVAL_CONTRACT_V1.required_artifacts
        assert "skill_spec_payload" in arts
        assert "initial_evidence" in arts


class TestReservedKindSkeletons:
    @pytest.mark.parametrize(
        ("contract", "expected_kind"),
        [
            (WRAPPER_TOOL_EVAL_CONTRACT_SKELETON, GrowthObjectKind.wrapper_tool),
            (PROCEDURE_SPEC_EVAL_CONTRACT_SKELETON, GrowthObjectKind.procedure_spec),
            (MEMORY_APP_SPEC_EVAL_CONTRACT_SKELETON, GrowthObjectKind.memory_application_spec),
        ],
    )
    def test_kind_matches(
        self, contract: GrowthEvalContract, expected_kind: GrowthObjectKind
    ) -> None:
        assert contract.object_kind == expected_kind

    @pytest.mark.parametrize(
        "contract",
        [
            WRAPPER_TOOL_EVAL_CONTRACT_SKELETON,
            PROCEDURE_SPEC_EVAL_CONTRACT_SKELETON,
            MEMORY_APP_SPEC_EVAL_CONTRACT_SKELETON,
        ],
    )
    def test_skeleton_has_required_checks(self, contract: GrowthEvalContract) -> None:
        assert len(contract.required_checks) > 0

    @pytest.mark.parametrize(
        "contract",
        [
            WRAPPER_TOOL_EVAL_CONTRACT_SKELETON,
            PROCEDURE_SPEC_EVAL_CONTRACT_SKELETON,
            MEMORY_APP_SPEC_EVAL_CONTRACT_SKELETON,
        ],
    )
    def test_skeleton_has_veto_conditions(self, contract: GrowthEvalContract) -> None:
        assert len(contract.veto_conditions) > 0

    @pytest.mark.parametrize(
        "contract",
        [
            WRAPPER_TOOL_EVAL_CONTRACT_SKELETON,
            PROCEDURE_SPEC_EVAL_CONTRACT_SKELETON,
            MEMORY_APP_SPEC_EVAL_CONTRACT_SKELETON,
        ],
    )
    def test_skeleton_frozen(self, contract: GrowthEvalContract) -> None:
        with pytest.raises(AttributeError):
            contract.version = 99  # type: ignore[misc]


class TestGetContract:
    @pytest.mark.parametrize("kind", list(GrowthObjectKind))
    def test_every_kind_has_contract(self, kind: GrowthObjectKind) -> None:
        contract = get_contract(kind)
        assert isinstance(contract, GrowthEvalContract)
        assert contract.object_kind == kind

    def test_soul_returns_v1(self) -> None:
        assert get_contract(GrowthObjectKind.soul) is SOUL_EVAL_CONTRACT_V1

    def test_skill_spec_returns_v1(self) -> None:
        assert get_contract(GrowthObjectKind.skill_spec) is SKILL_SPEC_EVAL_CONTRACT_V1

    def test_invalid_kind_raises(self) -> None:
        with pytest.raises(KeyError):
            get_contract("nonexistent")  # type: ignore[arg-type]


class TestGrowthEvalResultContractPinning:
    def test_default_contract_fields(self) -> None:
        result = GrowthEvalResult(passed=True)
        assert result.contract_id == ""
        assert result.contract_version == 0

    def test_explicit_contract_fields(self) -> None:
        result = GrowthEvalResult(
            passed=True,
            contract_id="soul_v1",
            contract_version=1,
            summary="All checks passed",
        )
        assert result.contract_id == "soul_v1"
        assert result.contract_version == 1

    def test_backward_compatible_construction(self) -> None:
        """Existing code that does not pass contract fields still works."""
        result = GrowthEvalResult(
            passed=False,
            checks=[{"name": "foo", "passed": False, "detail": "bad"}],
            summary="Failed",
        )
        assert result.passed is False
        assert result.contract_id == ""
        assert result.contract_version == 0


class TestContractImmutabilityInvariants:
    """Verify ADR 0054 immutability invariants at the type level."""

    def test_contract_id_unique_per_kind(self) -> None:
        """Each kind's contract has a distinct contract_id."""
        ids = set()
        for kind in GrowthObjectKind:
            contract = get_contract(kind)
            assert contract.contract_id not in ids, f"Duplicate contract_id: {contract.contract_id}"
            ids.add(contract.contract_id)

    def test_all_contracts_version_positive(self) -> None:
        for kind in GrowthObjectKind:
            contract = get_contract(kind)
            assert contract.version >= 1
