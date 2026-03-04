"""Health check data models for preflight and doctor subsystems.

CheckStatus / CheckResult / PreflightReport — structured evidence output
for startup checks (preflight) and runtime diagnostics (doctor).
ComponentHealthTracker — in-process health state for readiness evaluation.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class CheckStatus(enum.Enum):
    """Three-state health check outcome."""

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    """Single health check outcome with diagnostic evidence."""

    name: str
    status: CheckStatus
    evidence: str
    impact: str
    next_action: str


@dataclass
class DoctorReport:
    """Aggregated doctor check results."""

    checks: list[CheckResult] = field(default_factory=list)
    deep: bool = False

    @property
    def passed(self) -> bool:
        """True only when no check has FAIL status."""
        return all(c.status != CheckStatus.FAIL for c in self.checks)

    def summary(self) -> str:
        """Format a human-readable summary of all checks."""
        lines: list[str] = []
        for c in self.checks:
            lines.append(f"[{c.status.value.upper():4s}] {c.name}: {c.evidence}")
        mode = "deep" if self.deep else "standard"
        status = "PASS" if self.passed else "FAIL"
        lines.append(f"--- doctor {status} ({len(self.checks)} checks, mode={mode}) ---")
        return "\n".join(lines)


@dataclass
class PreflightReport:
    """Aggregated preflight check results."""

    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """True only when no check has FAIL status."""
        return all(c.status != CheckStatus.FAIL for c in self.checks)

    def summary(self) -> str:
        """Format a human-readable summary of all checks."""
        lines: list[str] = []
        for c in self.checks:
            lines.append(f"[{c.status.value.upper():4s}] {c.name}: {c.evidence}")
        status = "PASS" if self.passed else "FAIL"
        lines.append(f"--- preflight {status} ({len(self.checks)} checks) ---")
        return "\n".join(lines)


class ComponentHealthTracker:
    """In-process health state for readiness evaluation.

    Updated by model_client (provider) and app lifespan (telegram).
    Read by /health/ready endpoint. No locks needed: single-process asyncio.
    """

    PROVIDER_FAILURE_THRESHOLD = 5

    def __init__(self) -> None:
        self.telegram_healthy: bool = True
        self.telegram_error: str | None = None
        self.provider_consecutive_failures: int = 0

    @property
    def provider_healthy(self) -> bool:
        return self.provider_consecutive_failures < self.PROVIDER_FAILURE_THRESHOLD

    def record_provider_success(self) -> None:
        self.provider_consecutive_failures = 0

    def record_provider_failure(self) -> None:
        self.provider_consecutive_failures += 1

    def record_telegram_failure(self, error: str) -> None:
        self.telegram_healthy = False
        self.telegram_error = error
