"""Tests for health endpoints: /health, /health/live, /health/ready (three-layer readiness)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.gateway.app import app
from src.infra.health import CheckResult, CheckStatus, ComponentHealthTracker, PreflightReport


def _ok(name: str) -> CheckResult:
    return CheckResult(name=name, status=CheckStatus.OK, evidence="ok", impact="", next_action="")


def _fail(name: str, evidence: str = "error") -> CheckResult:
    return CheckResult(
        name=name, status=CheckStatus.FAIL, evidence=evidence, impact="bad", next_action="fix"
    )


def _setup_app_state(
    *,
    preflight_checks: list[CheckResult] | None = None,
    tracker: ComponentHealthTracker | None = None,
) -> None:
    """Set up app.state for testing health endpoints (no lifespan)."""
    if preflight_checks is not None:
        app.state.preflight_report = PreflightReport(checks=preflight_checks)
    elif not hasattr(app.state, "preflight_report"):
        app.state.preflight_report = None

    app.state.settings = object()  # placeholder; run_readiness_checks is mocked
    app.state.db_engine = object()
    app.state.health_tracker = tracker or ComponentHealthTracker()


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestHealthLive:
    @pytest.mark.asyncio
    async def test_liveness(self) -> None:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health/live")
        assert resp.status_code == 200
        assert resp.json() == {"status": "alive"}


class TestHealthReady:
    @pytest.mark.asyncio
    async def test_not_ready_when_no_report(self) -> None:
        app.state.preflight_report = None
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health/ready")
        data = resp.json()
        assert data["status"] == "not_ready"
        assert data["checks"] == {}

    @pytest.mark.asyncio
    async def test_not_ready_when_preflight_failed(self) -> None:
        app.state.preflight_report = PreflightReport(checks=[_fail("db", "unreachable")])
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health/ready")
        data = resp.json()
        assert data["status"] == "not_ready"
        assert data["checks"]["db"]["status"] == "fail"

    @pytest.mark.asyncio
    @patch("src.gateway.app.run_readiness_checks")
    async def test_ready_all_healthy(self, mock_readiness: AsyncMock) -> None:
        mock_readiness.return_value = PreflightReport(
            checks=[_ok("db_connection"), _ok("schema_tables")]
        )
        _setup_app_state(
            preflight_checks=[
                _ok("active_provider"),
                _ok("db_connection"),
                _ok("soul_reconcile"),
            ],
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health/ready")
        data = resp.json()
        assert data["status"] == "ready"
        # Layer 1: real-time
        assert "db_connection" in data["checks"]
        assert "schema_tables" in data["checks"]
        # Layer 2: startup latched
        assert "active_provider" in data["checks"]
        assert "soul_reconcile" in data["checks"]

    @pytest.mark.asyncio
    @patch("src.gateway.app.run_readiness_checks")
    async def test_not_ready_db_down(self, mock_readiness: AsyncMock) -> None:
        """Layer 1: real-time DB failure → not_ready."""
        mock_readiness.return_value = PreflightReport(
            checks=[_fail("db_connection", "connection refused")]
        )
        _setup_app_state(
            preflight_checks=[_ok("active_provider"), _ok("db_connection"), _ok("soul_reconcile")],
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health/ready")
        data = resp.json()
        assert data["status"] == "not_ready"
        assert data["checks"]["db_connection"]["status"] == "fail"

    @pytest.mark.asyncio
    @patch("src.gateway.app.run_readiness_checks")
    async def test_not_ready_telegram_unhealthy(self, mock_readiness: AsyncMock) -> None:
        """Layer 3: telegram polling fatal → not_ready."""
        mock_readiness.return_value = PreflightReport(checks=[_ok("db_connection")])
        tracker = ComponentHealthTracker()
        tracker.record_telegram_failure("Connection reset")
        _setup_app_state(
            preflight_checks=[_ok("active_provider"), _ok("soul_reconcile")],
            tracker=tracker,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health/ready")
        data = resp.json()
        assert data["status"] == "not_ready"
        assert "telegram_runtime" in data["checks"]
        assert data["checks"]["telegram_runtime"]["status"] == "fail"
        assert "Connection reset" in data["checks"]["telegram_runtime"]["evidence"]

    @pytest.mark.asyncio
    @patch("src.gateway.app.run_readiness_checks")
    async def test_not_ready_provider_consecutive_failures(
        self, mock_readiness: AsyncMock
    ) -> None:
        """Layer 3: provider consecutive failures >= threshold → not_ready."""
        mock_readiness.return_value = PreflightReport(checks=[_ok("db_connection")])
        tracker = ComponentHealthTracker()
        for _ in range(ComponentHealthTracker.PROVIDER_FAILURE_THRESHOLD):
            tracker.record_provider_failure()
        _setup_app_state(
            preflight_checks=[_ok("active_provider"), _ok("soul_reconcile")],
            tracker=tracker,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health/ready")
        data = resp.json()
        assert data["status"] == "not_ready"
        assert "provider_runtime" in data["checks"]
        assert data["checks"]["provider_runtime"]["status"] == "fail"

    @pytest.mark.asyncio
    @patch("src.gateway.app.run_readiness_checks")
    async def test_provider_below_threshold_still_ready(
        self, mock_readiness: AsyncMock
    ) -> None:
        """Provider failures below threshold → still ready."""
        mock_readiness.return_value = PreflightReport(checks=[_ok("db_connection")])
        tracker = ComponentHealthTracker()
        for _ in range(ComponentHealthTracker.PROVIDER_FAILURE_THRESHOLD - 1):
            tracker.record_provider_failure()
        _setup_app_state(
            preflight_checks=[_ok("active_provider"), _ok("soul_reconcile")],
            tracker=tracker,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health/ready")
        data = resp.json()
        assert data["status"] == "ready"
        assert "provider_runtime" not in data["checks"]

    @pytest.mark.asyncio
    @patch("src.gateway.app.run_readiness_checks")
    async def test_provider_success_resets_failure_count(
        self, mock_readiness: AsyncMock
    ) -> None:
        """Provider success resets consecutive failure count."""
        mock_readiness.return_value = PreflightReport(checks=[_ok("db_connection")])
        tracker = ComponentHealthTracker()
        for _ in range(ComponentHealthTracker.PROVIDER_FAILURE_THRESHOLD - 1):
            tracker.record_provider_failure()
        tracker.record_provider_success()  # reset
        _setup_app_state(
            preflight_checks=[_ok("active_provider"), _ok("soul_reconcile")],
            tracker=tracker,
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health/ready")
        data = resp.json()
        assert data["status"] == "ready"
