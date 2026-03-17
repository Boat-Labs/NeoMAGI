"""Domain types for the growth governance kernel.

Defines the vocabulary layer: object kinds, lifecycle statuses,
proposals, eval results, eval contracts, and policy structures.

GrowthLifecycleStatus MUST stay aligned with
``src.memory.evolution.VALID_STATUSES``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class GrowthObjectKind(StrEnum):
    """Enumeration of all recognised growth object kinds.

    P2-M1a: only ``soul`` is onboarded; the rest are reserved.
    """

    soul = "soul"
    skill_spec = "skill_spec"
    wrapper_tool = "wrapper_tool"
    procedure_spec = "procedure_spec"
    memory_application_spec = "memory_application_spec"


class GrowthOnboardingState(StrEnum):
    """Whether a growth object kind has a live adapter."""

    onboarded = "onboarded"
    reserved = "reserved"


class GrowthLifecycleStatus(StrEnum):
    """Lifecycle statuses for governed growth objects.

    Aligned with ``src.memory.evolution.VALID_STATUSES``:
    ``{"active", "proposed", "superseded", "rolled_back", "vetoed"}``.
    """

    proposed = "proposed"
    active = "active"
    superseded = "superseded"
    rolled_back = "rolled_back"
    vetoed = "vetoed"


class PassRuleKind(StrEnum):
    """Enumeration of pass/fail judgment strategies for eval contracts.

    ``all_required``: every required check must pass.
    ``hard_pass_and_threshold``: hard checks must all pass; soft checks
    need to meet a configurable threshold.
    """

    all_required = "all_required"
    hard_pass_and_threshold = "hard_pass_and_threshold"


@dataclass(frozen=True)
class GrowthProposal:
    """A proposal to mutate a governed growth object."""

    object_kind: GrowthObjectKind
    object_id: str
    intent: str
    risk_notes: str
    diff_summary: str
    evidence_refs: list[str] = field(default_factory=list)
    payload: dict[str, object] = field(default_factory=dict)
    proposed_by: str = "agent"


@dataclass(frozen=True)
class GrowthEvalContract:
    """Immutable, versioned, object-scoped eval contract for a growth object kind.

    Defines *what* must be checked and how pass/fail is judged.
    Adapters *execute* the contract; proposals cannot modify their own judge.

    See ADR 0054 and the four-layer structure:
    Boundary gates → Effect evidence → Scope claim → Efficiency metrics.

    Immutability invariants (ADR 0054 §3):
    1. Judge isolation — proposal cannot modify its judge / harness.
    2. Contract pinning — every eval run binds a fixed contract_id + version.
    3. Non-retroactivity — contract upgrades do not rewrite past conclusions.
    4. Ownership split — object change and contract change never in same proposal.
    5. Fixed keep/revert — pass/veto/rollback rules fixed before eval, not after.
    """

    contract_id: str
    object_kind: GrowthObjectKind
    version: int
    mutable_surface: tuple[str, ...]
    immutable_harness: tuple[str, ...]
    required_checks: tuple[str, ...]
    required_artifacts: tuple[str, ...]
    pass_rule_kind: PassRuleKind
    pass_rule_params: tuple[str, ...]
    veto_conditions: tuple[str, ...]
    rollback_preconditions: tuple[str, ...]
    budget_limits: tuple[str, ...]


@dataclass(frozen=True)
class GrowthEvalResult:
    """Result of evaluating a growth proposal under a pinned contract.

    ``contract_id`` and ``contract_version`` trace which contract was used,
    enabling audit and non-retroactivity (ADR 0054 §1a).
    """

    passed: bool
    checks: list[dict] = field(default_factory=list)
    summary: str = ""
    contract_id: str = ""
    contract_version: int = 0


@dataclass(frozen=True)
class GrowthKindPolicy:
    """Per-kind governance metadata."""

    kind: GrowthObjectKind
    onboarding_state: GrowthOnboardingState
    requires_explicit_approval: bool
    adapter_name: str | None
    notes: str = ""


@dataclass(frozen=True)
class PromotionPolicy:
    """Cross-kind promotion rule (schema only in P2-M1a)."""

    from_kind: GrowthObjectKind
    to_kind: GrowthObjectKind
    required_evidence: list[str] = field(default_factory=list)
    required_tests: list[str] = field(default_factory=list)
    risk_gate: str = ""
