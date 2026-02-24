"""Tests for runtime guardrail (Phase 0, ADR 0035).

Covers:
- CoreSafetyContract creation and immutability
- Anchor extraction from workspace files
- Lazy hash refresh
- check_pre_llm_guard: detection only (no blocking)
- check_pre_tool_guard: risk-gated fail-closed
- Audit log events (guardrail_warning / guardrail_blocked / guardrail_degraded)
- Guard state per-iteration refresh
"""

from __future__ import annotations

from pathlib import Path

import pytest
import structlog

from src.agent.guardrail import (
    CoreSafetyContract,
    GuardCheckResult,
    check_pre_llm_guard,
    check_pre_tool_guard,
    load_contract,
    maybe_refresh_contract,
)
from src.tools.base import RiskLevel


class TestCoreSafetyContract:
    def test_create(self) -> None:
        c = CoreSafetyContract(
            anchors=("anchor1", "anchor2"),
            constraints=("c1",),
            source_hash="abc",
        )
        assert c.anchors == ("anchor1", "anchor2")
        assert c.constraints == ("c1",)
        assert c.source_hash == "abc"

    def test_frozen(self) -> None:
        c = CoreSafetyContract(anchors=(), constraints=())
        with pytest.raises(AttributeError):
            c.anchors = ("new",)  # type: ignore[misc]

    def test_empty_defaults(self) -> None:
        c = CoreSafetyContract(anchors=(), constraints=())
        assert c.source_hash == ""


class TestLoadContract:
    def test_load_from_workspace_with_agents_md(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# Magi Core Identity\n- **Safety**: always safe\n")
        c = load_contract(tmp_path)
        assert "Magi Core Identity" in c.anchors
        assert "Safety" in c.anchors
        assert c.source_hash != ""

    def test_load_empty_workspace(self, tmp_path: Path) -> None:
        c = load_contract(tmp_path)
        assert c.anchors == ()
        assert c.source_hash != ""

    def test_load_multiple_files(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# Agent Rules\n")
        (tmp_path / "USER.md").write_text("# User Preferences\n")
        (tmp_path / "SOUL.md").write_text("# Soul Identity\n- **Core**: be kind\n")
        c = load_contract(tmp_path)
        assert "Agent Rules" in c.anchors
        assert "User Preferences" in c.anchors
        assert "Soul Identity" in c.anchors
        assert "Core" in c.anchors


class TestMaybeRefreshContract:
    def test_no_refresh_when_hash_matches(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# Test\n")
        c1 = load_contract(tmp_path)
        c2 = maybe_refresh_contract(c1, tmp_path)
        assert c2 is c1  # same object, no reload

    def test_refresh_when_file_changes(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# Original\n")
        c1 = load_contract(tmp_path)
        (tmp_path / "AGENTS.md").write_text("# Updated\n")
        c2 = maybe_refresh_contract(c1, tmp_path)
        assert c2 is not c1
        assert "Updated" in c2.anchors

    def test_refresh_from_none(self, tmp_path: Path) -> None:
        (tmp_path / "AGENTS.md").write_text("# Fresh\n")
        c = maybe_refresh_contract(None, tmp_path)
        assert "Fresh" in c.anchors


class TestCheckPreLlmGuard:
    def test_all_anchors_visible_passes(self) -> None:
        contract = CoreSafetyContract(
            anchors=("anchor_a", "anchor_b"),
            constraints=(),
            source_hash="h",
        )
        result = check_pre_llm_guard(contract, "text with anchor_a and anchor_b here")
        assert result.passed is True
        assert result.missing_anchors == []

    def test_missing_anchor_fails(self) -> None:
        contract = CoreSafetyContract(
            anchors=("visible_one", "invisible_one"),
            constraints=(),
            source_hash="h",
        )
        result = check_pre_llm_guard(contract, "only visible_one is here")
        assert result.passed is False
        assert "invisible_one" in result.missing_anchors
        assert result.error_code == "GUARD_ANCHOR_MISSING"

    def test_none_contract_fails(self) -> None:
        result = check_pre_llm_guard(None, "any context")
        assert result.passed is False
        assert result.error_code == "GUARD_CONTRACT_UNAVAILABLE"

    def test_empty_anchors_fails(self) -> None:
        contract = CoreSafetyContract(anchors=(), constraints=(), source_hash="h")
        result = check_pre_llm_guard(contract, "any context")
        assert result.passed is False
        assert result.error_code == "GUARD_CONTRACT_UNAVAILABLE"

    def test_warning_audit_log_on_failure(self) -> None:
        contract = CoreSafetyContract(
            anchors=("missing_anchor",), constraints=(), source_hash="h"
        )
        cap = structlog.testing.LogCapture()
        structlog.configure(processors=[cap], wrapper_class=structlog.BoundLogger)
        try:
            check_pre_llm_guard(contract, "no anchors here")
            events = [e for e in cap.entries if e.get("event") == "guardrail_warning"]
            assert len(events) >= 1
            assert events[0]["error_code"] == "GUARD_ANCHOR_MISSING"
        finally:
            structlog.reset_defaults()

    def test_does_not_block_returns_result(self) -> None:
        """Pre-LLM guard always returns result (never raises)."""
        contract = CoreSafetyContract(
            anchors=("gone",), constraints=(), source_hash="h"
        )
        result = check_pre_llm_guard(contract, "empty")
        assert isinstance(result, GuardCheckResult)
        assert result.passed is False


class TestCheckPreToolGuard:
    def _failed_guard(self) -> GuardCheckResult:
        return GuardCheckResult(
            passed=False,
            missing_anchors=["anchor_x"],
            error_code="GUARD_ANCHOR_MISSING",
            detail="1 anchor(s) not visible",
        )

    def _passed_guard(self) -> GuardCheckResult:
        return GuardCheckResult(passed=True)

    def test_high_risk_guard_failed_blocks(self) -> None:
        result = check_pre_tool_guard(
            self._failed_guard(), "dangerous_tool", RiskLevel.high
        )
        assert result is not None
        assert result.passed is False
        assert result.error_code == "GUARD_ANCHOR_MISSING"

    def test_low_risk_guard_failed_allows(self) -> None:
        result = check_pre_tool_guard(
            self._failed_guard(), "safe_tool", RiskLevel.low
        )
        assert result is None  # None = proceed

    def test_high_risk_guard_passed_allows(self) -> None:
        result = check_pre_tool_guard(
            self._passed_guard(), "dangerous_tool", RiskLevel.high
        )
        assert result is None

    def test_low_risk_guard_passed_allows(self) -> None:
        result = check_pre_tool_guard(
            self._passed_guard(), "safe_tool", RiskLevel.low
        )
        assert result is None

    def test_blocked_audit_log(self) -> None:
        cap = structlog.testing.LogCapture()
        structlog.configure(processors=[cap], wrapper_class=structlog.BoundLogger)
        try:
            check_pre_tool_guard(
                self._failed_guard(), "risky_tool", RiskLevel.high
            )
            blocked = [e for e in cap.entries if e.get("event") == "guardrail_blocked"]
            assert len(blocked) == 1
            assert blocked[0]["tool_name"] == "risky_tool"
        finally:
            structlog.reset_defaults()

    def test_degraded_audit_log(self) -> None:
        cap = structlog.testing.LogCapture()
        structlog.configure(processors=[cap], wrapper_class=structlog.BoundLogger)
        try:
            check_pre_tool_guard(
                self._failed_guard(), "safe_tool", RiskLevel.low
            )
            degraded = [e for e in cap.entries if e.get("event") == "guardrail_degraded"]
            assert len(degraded) == 1
            assert degraded[0]["tool_name"] == "safe_tool"
        finally:
            structlog.reset_defaults()


class TestGuardStatePerIteration:
    """Verify guard_state is produced fresh each LLM iteration."""

    def test_different_contexts_produce_different_results(self) -> None:
        contract = CoreSafetyContract(
            anchors=("special_anchor",), constraints=(), source_hash="h"
        )
        # Iteration 1: anchor missing
        r1 = check_pre_llm_guard(contract, "no anchor here")
        assert r1.passed is False

        # Iteration 2: anchor now visible (e.g., tool result added to context)
        r2 = check_pre_llm_guard(contract, "text with special_anchor present")
        assert r2.passed is True

        # They are independent results
        assert r1 is not r2
