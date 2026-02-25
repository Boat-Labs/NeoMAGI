"""Tests for M6 eval T11/T12 judgment logic (P1-2 fix).

Verifies that tool tasks require actual tool invocation to pass —
a text response without the target tool_call must be a FAIL.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def _fake_send_and_collect(text: str, tool_calls: list | None = None, errors: list | None = None):
    """Build a mock _send_and_collect coroutine returning a fixed response."""

    async def _mock(*_args, **_kwargs):
        return {
            "text": text,
            "tool_calls": tool_calls or [],
            "errors": errors or [],
        }

    return _mock


class TestT11Judgment:
    """T11 (single tool call) must trigger current_time to PASS."""

    @pytest.mark.asyncio
    async def test_t11_pass_when_tool_triggered(self):
        from scripts.m6_eval import run_t11

        with patch("scripts.m6_eval._send_and_collect", new=_fake_send_and_collect(
            text="It is 3pm.",
            tool_calls=[{"tool_name": "current_time"}],
        )):
            r = await run_t11(AsyncMock(), "openai")

        assert r.status == "PASS"

    @pytest.mark.asyncio
    async def test_t11_fail_when_no_tool_call(self):
        from scripts.m6_eval import run_t11

        with patch("scripts.m6_eval._send_and_collect", new=_fake_send_and_collect(
            text="I don't know the time.",
        )):
            r = await run_t11(AsyncMock(), "openai")

        assert r.status == "FAIL"
        assert "current_time" in r.detail

    @pytest.mark.asyncio
    async def test_t11_fail_on_errors(self):
        from scripts.m6_eval import run_t11

        with patch("scripts.m6_eval._send_and_collect", new=_fake_send_and_collect(
            text="", errors=[{"code": "INTERNAL_ERROR"}],
        )):
            r = await run_t11(AsyncMock(), "openai")

        assert r.status == "FAIL"


class TestT12Judgment:
    """T12 (tool chain) must trigger memory_search to PASS."""

    @pytest.mark.asyncio
    async def test_t12_pass_when_tool_triggered(self):
        from scripts.m6_eval import run_t12

        with patch("scripts.m6_eval._send_and_collect", new=_fake_send_and_collect(
            text="Found: project goals are X.",
            tool_calls=[{"tool_name": "memory_search"}],
        )):
            r = await run_t12(AsyncMock(), "openai")

        assert r.status == "PASS"

    @pytest.mark.asyncio
    async def test_t12_fail_when_no_tool_call(self):
        from scripts.m6_eval import run_t12

        with patch("scripts.m6_eval._send_and_collect", new=_fake_send_and_collect(
            text="I don't have memory search results.",
        )):
            r = await run_t12(AsyncMock(), "openai")

        assert r.status == "FAIL"
        assert "memory_search" in r.detail

    @pytest.mark.asyncio
    async def test_t12_fail_on_errors(self):
        from scripts.m6_eval import run_t12

        with patch("scripts.m6_eval._send_and_collect", new=_fake_send_and_collect(
            text="", errors=[{"code": "TOOL_ERROR"}],
        )):
            r = await run_t12(AsyncMock(), "openai")

        assert r.status == "FAIL"
