"""Tests for src/infra/health.py data models."""

from __future__ import annotations

from src.infra.health import CheckResult, CheckStatus, PreflightReport


class TestCheckStatus:
    def test_values(self) -> None:
        assert CheckStatus.OK.value == "ok"
        assert CheckStatus.WARN.value == "warn"
        assert CheckStatus.FAIL.value == "fail"

    def test_enum_members(self) -> None:
        assert set(CheckStatus) == {CheckStatus.OK, CheckStatus.WARN, CheckStatus.FAIL}


class TestCheckResult:
    def test_create(self) -> None:
        r = CheckResult(
            name="test",
            status=CheckStatus.OK,
            evidence="all good",
            impact="",
            next_action="",
        )
        assert r.name == "test"
        assert r.status == CheckStatus.OK
        assert r.evidence == "all good"

    def test_frozen(self) -> None:
        r = CheckResult(
            name="test", status=CheckStatus.OK, evidence="ok", impact="", next_action=""
        )
        try:
            r.name = "changed"  # type: ignore[misc]
            assert False, "Should be frozen"
        except AttributeError:
            pass


class TestPreflightReport:
    def _ok(self, name: str = "c1") -> CheckResult:
        return CheckResult(
            name=name, status=CheckStatus.OK, evidence="ok", impact="", next_action=""
        )

    def _warn(self, name: str = "c2") -> CheckResult:
        return CheckResult(
            name=name, status=CheckStatus.WARN,
            evidence="warning", impact="minor", next_action="fix",
        )

    def _fail(self, name: str = "c3") -> CheckResult:
        return CheckResult(
            name=name, status=CheckStatus.FAIL,
            evidence="error", impact="major", next_action="fix now",
        )

    def test_all_ok_passes(self) -> None:
        report = PreflightReport(checks=[self._ok(), self._ok("c2")])
        assert report.passed is True

    def test_warn_still_passes(self) -> None:
        report = PreflightReport(checks=[self._ok(), self._warn()])
        assert report.passed is True

    def test_fail_does_not_pass(self) -> None:
        report = PreflightReport(checks=[self._ok(), self._fail()])
        assert report.passed is False

    def test_empty_report_passes(self) -> None:
        report = PreflightReport()
        assert report.passed is True

    def test_summary_contains_check_names(self) -> None:
        report = PreflightReport(checks=[self._ok("db"), self._warn("trigger")])
        s = report.summary()
        assert "db" in s
        assert "trigger" in s
        assert "PASS" in s

    def test_summary_fail_label(self) -> None:
        report = PreflightReport(checks=[self._fail("db")])
        s = report.summary()
        assert "FAIL" in s
