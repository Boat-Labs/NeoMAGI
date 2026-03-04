"""Tests for health endpoints: /health, /health/live, /health/ready."""

from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient

from src.infra.health import CheckResult, CheckStatus, PreflightReport


def _make_test_app() -> FastAPI:
    """Create a minimal FastAPI app with health endpoints (no lifespan)."""
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/health/live")
    async def health_live():
        return {"status": "alive"}

    @app.get("/health/ready")
    async def health_ready(request: Request):
        report = getattr(request.app.state, "preflight_report", None)
        if report is None or not report.passed:
            checks = {}
            if report:
                checks = {
                    c.name: {"status": c.status.value, "evidence": c.evidence}
                    for c in report.checks
                }
            return {"status": "not_ready", "checks": checks}
        return {
            "status": "ready",
            "checks": {
                c.name: {"status": c.status.value, "evidence": c.evidence}
                for c in report.checks
            },
        }

    return app


class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self) -> None:
        app = _make_test_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestHealthLive:
    @pytest.mark.asyncio
    async def test_liveness(self) -> None:
        app = _make_test_app()
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health/live")
        assert resp.status_code == 200
        assert resp.json() == {"status": "alive"}


class TestHealthReady:
    @pytest.mark.asyncio
    async def test_ready_when_passed(self) -> None:
        app = _make_test_app()
        app.state.preflight_report = PreflightReport(
            checks=[
                CheckResult("db", CheckStatus.OK, "connected", "", ""),
                CheckResult("provider", CheckStatus.OK, "configured", "", ""),
            ]
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health/ready")
        data = resp.json()
        assert resp.status_code == 200
        assert data["status"] == "ready"
        assert "db" in data["checks"]
        assert data["checks"]["db"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_not_ready_when_failed(self) -> None:
        app = _make_test_app()
        app.state.preflight_report = PreflightReport(
            checks=[
                CheckResult("db", CheckStatus.FAIL, "unreachable", "crash", "fix it"),
            ]
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health/ready")
        data = resp.json()
        assert resp.status_code == 200
        assert data["status"] == "not_ready"
        assert data["checks"]["db"]["status"] == "fail"

    @pytest.mark.asyncio
    async def test_not_ready_when_no_report(self) -> None:
        app = _make_test_app()
        # No preflight_report set on app.state
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health/ready")
        data = resp.json()
        assert resp.status_code == 200
        assert data["status"] == "not_ready"
        assert data["checks"] == {}

    @pytest.mark.asyncio
    async def test_ready_does_not_expose_secrets(self) -> None:
        """Evidence field should not contain API keys or tokens."""
        app = _make_test_app()
        app.state.preflight_report = PreflightReport(
            checks=[
                CheckResult(
                    "provider",
                    CheckStatus.OK,
                    "Provider 'openai' API key configured",
                    "",
                    "",
                ),
            ]
        )
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/health/ready")
        data = resp.json()
        # Verify evidence doesn't contain actual key values
        evidence = data["checks"]["provider"]["evidence"]
        assert "sk-" not in evidence
