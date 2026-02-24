from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import structlog

from src.tools.base import RiskLevel

logger = structlog.get_logger()

# Structured error codes for audit trail (ADR 0035)
GUARDRAIL_ERROR_CODES = {
    "GUARD_ANCHOR_MISSING": "Core anchor(s) not visible in execution context",
    "GUARD_CONSTRAINT_VIOLATED": "Safety constraint violation detected",
    "GUARD_CONTRACT_UNAVAILABLE": "Core Safety Contract could not be loaded",
}

# Workspace files that contribute to the safety contract
CONTRACT_SOURCE_FILES = ("AGENTS.md", "USER.md", "SOUL.md")


@dataclass(frozen=True)
class CoreSafetyContract:
    """Immutable set of anchors that MUST remain visible in execution context.

    Source: extracted from AGENTS.md / USER.md / SOUL.md.
    Lifecycle: loaded at agent startup, lazily refreshed when source_hash changes.
    """

    anchors: tuple[str, ...]
    constraints: tuple[str, ...]
    source_hash: str = ""


@dataclass
class GuardCheckResult:
    """Result of a single guard checkpoint execution."""

    passed: bool
    missing_anchors: list[str] = field(default_factory=list)
    violated_constraints: list[str] = field(default_factory=list)
    error_code: str = ""
    detail: str = ""


def _compute_source_hash(workspace_dir: Path) -> str:
    """Compute combined hash of contract source files."""
    h = hashlib.sha256()
    for filename in CONTRACT_SOURCE_FILES:
        filepath = workspace_dir / filename
        if filepath.is_file():
            try:
                h.update(filepath.read_bytes())
            except OSError:
                pass
    return h.hexdigest()


def _extract_anchors_from_content(content: str) -> list[str]:
    """Extract anchor phrases from workspace file content.

    Anchors are first-level headings and lines starting with '- **' (bold list items).
    These represent key identity and constraint declarations.
    """
    anchors: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and len(stripped) > 2:
            anchors.append(stripped[2:].strip())
        elif stripped.startswith("- **"):
            end = stripped.find("**", 4)
            if end > 4:
                anchors.append(stripped[4:end].strip())
    return anchors


def load_contract(workspace_dir: Path) -> CoreSafetyContract:
    """Load CoreSafetyContract from workspace files.

    Extracts anchors from AGENTS.md / USER.md / SOUL.md.
    Returns contract with empty anchors if no source files exist.
    """
    source_hash = _compute_source_hash(workspace_dir)
    all_anchors: list[str] = []
    all_constraints: list[str] = []

    for filename in CONTRACT_SOURCE_FILES:
        filepath = workspace_dir / filename
        if not filepath.is_file():
            continue
        try:
            content = filepath.read_text(encoding="utf-8")
        except OSError:
            logger.exception("contract_source_read_error", file=filename)
            continue

        anchors = _extract_anchors_from_content(content)
        all_anchors.extend(anchors)

    return CoreSafetyContract(
        anchors=tuple(all_anchors),
        constraints=tuple(all_constraints),
        source_hash=source_hash,
    )


def maybe_refresh_contract(
    current: CoreSafetyContract | None, workspace_dir: Path
) -> CoreSafetyContract:
    """Lazily refresh contract if source files have changed (hash mismatch).

    Called before each guard check. Only reloads when hash differs.
    """
    new_hash = _compute_source_hash(workspace_dir)
    if current is not None and current.source_hash == new_hash:
        return current
    logger.info("contract_refreshed", old_hash=current.source_hash if current else "none")
    return load_contract(workspace_dir)


def check_pre_llm_guard(
    contract: CoreSafetyContract | None,
    execution_context: str,
) -> GuardCheckResult:
    """Verify all contract anchors are visible in the LLM execution context.

    Called BEFORE every LLM API call. Detection only, NOT a blocking gate.
    On failure: logs guardrail_warning, does NOT block LLM call.
    The returned guard_state is consumed by check_pre_tool_guard.
    """
    if contract is None or not contract.anchors:
        logger.warning(
            "guardrail_warning",
            error_code="GUARD_CONTRACT_UNAVAILABLE",
            detail="No contract loaded or empty anchors",
        )
        return GuardCheckResult(
            passed=False,
            error_code="GUARD_CONTRACT_UNAVAILABLE",
            detail="No contract loaded or empty anchors",
        )

    missing = [a for a in contract.anchors if a not in execution_context]

    if missing:
        logger.warning(
            "guardrail_warning",
            error_code="GUARD_ANCHOR_MISSING",
            missing_count=len(missing),
            missing_anchors=missing[:5],
        )
        return GuardCheckResult(
            passed=False,
            missing_anchors=missing,
            error_code="GUARD_ANCHOR_MISSING",
            detail=f"{len(missing)} anchor(s) not visible in execution context",
        )

    return GuardCheckResult(passed=True)


def check_pre_tool_guard(
    guard_state: GuardCheckResult,
    tool_name: str,
    tool_risk_level: RiskLevel,
) -> GuardCheckResult | None:
    """Gate tool execution based on guard state and tool risk level.

    Returns None if execution should proceed.
    Returns GuardCheckResult with error if execution should be blocked.

    If risk_level is HIGH and guard failed → fail-closed (blocked).
    If risk_level is LOW and guard failed → degraded (allowed with log).
    """
    if guard_state.passed:
        return None

    if tool_risk_level == RiskLevel.high:
        logger.warning(
            "guardrail_blocked",
            tool_name=tool_name,
            risk_level=tool_risk_level.value,
            error_code=guard_state.error_code,
            missing_anchors=guard_state.missing_anchors[:5],
        )
        return GuardCheckResult(
            passed=False,
            missing_anchors=guard_state.missing_anchors,
            error_code=guard_state.error_code or "GUARD_ANCHOR_MISSING",
            detail=f"High-risk tool '{tool_name}' blocked: {guard_state.detail}",
        )

    # Low risk: allow degraded execution
    logger.warning(
        "guardrail_degraded",
        tool_name=tool_name,
        risk_level=tool_risk_level.value,
        error_code=guard_state.error_code,
    )
    return None
