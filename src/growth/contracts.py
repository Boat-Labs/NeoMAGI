"""Hardcoded eval contract profiles for growth object kinds.

Each profile is a frozen ``GrowthEvalContract`` constant — the adapter
pins it before running evaluation.  Contract changes require a new
version constant, not a mutation of an existing one (ADR 0054 §3).

P2-M1 landing form: doc + code declaration; no DB / registry object.

Four-layer structure per contract:
    Boundary gates  → hard gate, checked first
    Effect evidence → minimal effectiveness proof
    Scope claim     → determines extra evidence threshold
    Efficiency metrics → quality-gated, never sole apply signal
"""

from __future__ import annotations

from src.growth.types import GrowthEvalContract, GrowthObjectKind, PassRuleKind

# ── soul: first formal contract profile (WP2) ──

SOUL_EVAL_CONTRACT_V1 = GrowthEvalContract(
    contract_id="soul_v1",
    object_kind=GrowthObjectKind.soul,
    version=1,
    mutable_surface=(
        "SOUL.md content",
        "soul_versions proposal/eval payload",
    ),
    immutable_harness=(
        "deterministic content checks",
        "apply guard logic",
        "rollback/veto machinery",
        "audit completeness checks",
    ),
    required_checks=(
        # Boundary gates (names MUST match EvolutionEngine._build_eval_checks output)
        "content_coherence",  # includes non-empty check
        "size_limit",
        "diff_sanity",
    ),
    required_artifacts=(
        "intent",
        "risk_notes",
        "diff_summary",
        "evidence_refs",
    ),
    pass_rule_kind=PassRuleKind.all_required,
    pass_rule_params=(),
    veto_conditions=(
        "eval_not_passed_before_apply",
        "content_empty",
        "content_incoherent",
    ),
    rollback_preconditions=(
        "previous_active_version_exists",
        "apply_artifact_reversible",
        "rollback_target_known",
    ),
    budget_limits=(),
)

# ── skill_spec: P2-M1b onboarding contract (WP3) ──

SKILL_SPEC_EVAL_CONTRACT_V1 = GrowthEvalContract(
    contract_id="skill_spec_v1",
    object_kind=GrowthObjectKind.skill_spec,
    version=1,
    mutable_surface=(
        "SkillSpec fields",
        "SkillEvidence fields",
        "resolver/projector consumption surface",
    ),
    immutable_harness=(
        "schema validation checks",
        "precondition consistency checks",
        "resolution/projection regression suite",
        "negative evidence semantics rules",
    ),
    required_checks=(
        # Boundary gates
        "schema_validity",
        "activation_correctness",
        "projection_safety",
        # Effect evidence
        "learning_discipline",
        # Scope claim
        "scope_claim_consistency",
    ),
    required_artifacts=(
        "intent",
        "risk_notes",
        "diff_summary",
        "skill_spec_payload",
        "initial_evidence",
    ),
    pass_rule_kind=PassRuleKind.all_required,
    pass_rule_params=(),
    veto_conditions=(
        "schema_invalid",
        "activation_tags_contradictory",
        "preconditions_self_contradictory",
        "prompt_injection_risk",
        "negative_evidence_from_non_deterministic_signal",
    ),
    rollback_preconditions=(
        "previous_version_exists_or_disable_possible",
        "apply_artifact_reversible",
    ),
    budget_limits=(
        "delta_budget_per_skill_max_3",
        "total_delta_budget_max_9",
    ),
)

# ── wrapper_tool: contract skeleton (WP4, historical — superseded by V1) ──

WRAPPER_TOOL_EVAL_CONTRACT_SKELETON = GrowthEvalContract(
    contract_id="wrapper_tool_skeleton_v1",
    object_kind=GrowthObjectKind.wrapper_tool,
    version=1,
    mutable_surface=(
        "wrapper schema",
        "tool binding",
        "implementation code",
        "deny behavior",
    ),
    immutable_harness=(
        "typed I/O validation",
        "permission boundary checks",
        "dry-run/smoke test suite",
        "tool deny/error semantics checks",
    ),
    required_checks=(
        # Boundary gates
        "typed_io_validation",
        "permission_boundary",
        "dry_run_smoke",
        # Effect evidence
        "before_after_cases",
        # Scope claim
        "scope_claim_consistency",
    ),
    required_artifacts=(
        "intent",
        "risk_notes",
        "diff_summary",
        "tool_schema",
        "smoke_test_results",
    ),
    pass_rule_kind=PassRuleKind.all_required,
    pass_rule_params=(),
    veto_conditions=(
        "typed_io_mismatch",
        "permission_boundary_violation",
        "deny_semantics_broken",
    ),
    rollback_preconditions=(
        "previous_version_exists",
        "tool_binding_reversible",
    ),
    budget_limits=(),
)

# ── wrapper_tool: first formal contract profile (WP4, P2-M1c) ──

WRAPPER_TOOL_EVAL_CONTRACT_V1 = GrowthEvalContract(
    contract_id="wrapper_tool_eval_v1",
    object_kind=GrowthObjectKind.wrapper_tool,
    version=1,
    mutable_surface=(
        "wrapper schema",
        "tool binding / implementation_ref",
        "implementation code",
        "deny behavior / deny_semantics",
        "scope_claim declaration",
    ),
    immutable_harness=(
        "typed I/O validation checks",
        "permission boundary checks",
        "dry-run/smoke test suite",
        "before/after case corpus",
        "scope claim consistency rules",
    ),
    required_checks=(
        # Boundary gates — deterministic hard checks
        "typed_io_validation",  # input/output schema 是否合法
        "permission_boundary",  # 权限边界是否合规
        "dry_run_smoke",  # dry-run/smoke test 是否通过
        # Effect evidence
        "before_after_cases",  # 前后对比证据
        # Scope claim
        "scope_claim_consistency",  # scope_claim 与实际行为是否一致
    ),
    required_artifacts=(
        "intent",
        "risk_notes",
        "diff_summary",
        "tool_schema",
        "smoke_test_results",
    ),
    pass_rule_kind=PassRuleKind.all_required,
    pass_rule_params=(),
    veto_conditions=(
        "typed_io_mismatch",
        "permission_boundary_violation",
        "deny_semantics_broken",
        "scope_claim_contradicts_behavior",
    ),
    rollback_preconditions=(
        "previous_version_exists",
        "tool_binding_reversible",
        "no_active_consumers",
    ),
    budget_limits=(),
)

# ── procedure_spec: contract skeleton (WP4, historical — superseded by V1) ──

PROCEDURE_SPEC_EVAL_CONTRACT_SKELETON = GrowthEvalContract(
    contract_id="procedure_spec_skeleton_v1",
    object_kind=GrowthObjectKind.procedure_spec,
    version=1,
    mutable_surface=(
        "procedure spec",
        "state/guard/transition definitions",
    ),
    immutable_harness=(
        "deterministic transition suite",
        "interrupt/resume checks",
        "checkpoint recoverability checks",
    ),
    required_checks=(
        # Boundary gates
        "transition_determinism",
        "guard_completeness",
        "interrupt_resume_safety",
        # Effect evidence
        "checkpoint_recoverability",
        # Scope claim
        "scope_claim_consistency",
    ),
    required_artifacts=(
        "intent",
        "risk_notes",
        "diff_summary",
        "transition_table",
        "checkpoint_strategy",
    ),
    pass_rule_kind=PassRuleKind.all_required,
    pass_rule_params=(),
    veto_conditions=(
        "non_deterministic_transition",
        "unrecoverable_checkpoint",
        "guard_gap",
    ),
    rollback_preconditions=(
        "previous_version_exists",
        "no_active_instances_running",
    ),
    budget_limits=(),
)

# ── procedure_spec: first formal contract profile (WP4, P2-M2c) ──

PROCEDURE_SPEC_EVAL_CONTRACT_V1 = GrowthEvalContract(
    contract_id="procedure_spec_v1",
    object_kind=GrowthObjectKind.procedure_spec,
    version=1,
    mutable_surface=(
        "procedure spec",
        "state/guard/transition definitions",
        "context_model binding",
        "allowed_modes declaration",
    ),
    immutable_harness=(
        "deterministic transition checks",
        "guard completeness checks",
        "interrupt/resume safety checks",
        "checkpoint recoverability checks",
        "scope claim consistency checks",
    ),
    required_checks=(
        # Boundary gates — deterministic hard checks
        "transition_determinism",
        "guard_completeness",
        "interrupt_resume_safety",
        # Effect evidence
        "checkpoint_recoverability",
        # Scope claim
        "scope_claim_consistency",
    ),
    required_artifacts=(
        "intent",
        "risk_notes",
        "diff_summary",
        "transition_table",
        "checkpoint_strategy",
    ),
    pass_rule_kind=PassRuleKind.all_required,
    pass_rule_params=(),
    veto_conditions=(
        "non_deterministic_transition",
        "unrecoverable_checkpoint",
        "guard_gap",
        "scope_claim_contradicts_allowed_modes",
    ),
    rollback_preconditions=(
        "no_active_instances_running",
        "disable_possible",
    ),
    budget_limits=(),
)

# ── memory_application_spec: contract skeleton (WP4, deferred to P2-M3) ──

MEMORY_APP_SPEC_EVAL_CONTRACT_SKELETON = GrowthEvalContract(
    contract_id="memory_app_spec_skeleton_v1",
    object_kind=GrowthObjectKind.memory_application_spec,
    version=1,
    mutable_surface=("memory application spec",),
    immutable_harness=(
        "retrieval/share boundary checks",
        "quality eval checks",
        "scope correctness checks",
    ),
    required_checks=(
        # Boundary gates
        "retrieval_boundary",
        "share_boundary",
        # Effect evidence
        "quality_eval",
        # Scope claim
        "scope_correctness",
    ),
    required_artifacts=(
        "intent",
        "risk_notes",
        "diff_summary",
    ),
    pass_rule_kind=PassRuleKind.all_required,
    pass_rule_params=(),
    veto_conditions=(
        "retrieval_boundary_violation",
        "share_boundary_violation",
    ),
    rollback_preconditions=("previous_version_exists",),
    budget_limits=(),
)

# ── registry: kind → active contract ──

_CONTRACTS: dict[GrowthObjectKind, GrowthEvalContract] = {
    GrowthObjectKind.soul: SOUL_EVAL_CONTRACT_V1,
    GrowthObjectKind.skill_spec: SKILL_SPEC_EVAL_CONTRACT_V1,
    GrowthObjectKind.wrapper_tool: WRAPPER_TOOL_EVAL_CONTRACT_V1,
    GrowthObjectKind.procedure_spec: PROCEDURE_SPEC_EVAL_CONTRACT_V1,
    GrowthObjectKind.memory_application_spec: MEMORY_APP_SPEC_EVAL_CONTRACT_SKELETON,
}


def get_contract(kind: GrowthObjectKind) -> GrowthEvalContract:
    """Return the active eval contract for *kind*.

    Raises ``KeyError`` if no contract is registered (should not happen
    given the exhaustive dict, but fail-closed is better than silent).
    """
    return _CONTRACTS[kind]
