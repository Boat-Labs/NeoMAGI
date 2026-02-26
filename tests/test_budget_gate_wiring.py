"""Tests for BudgetGate wiring in _handle_chat_send (ADR 0041).

Verifies gateway-level integration: reserve/settle lifecycle, denied paths,
error handling, and parameter passthrough. Uses mocks (no real DB).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.gateway.app import DEFAULT_RESERVE_EUR, _handle_chat_send
from src.gateway.budget_gate import Reservation

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_handle_message(*_args, **_kwargs):
    """Empty async generator — simulates handle_message with no events."""
    return
    yield  # pragma: no cover — makes this an async generator


async def _failing_handle_message(*_args, **_kwargs):
    """Async generator that raises after starting iteration."""
    raise RuntimeError("agent_loop_exploded")
    yield  # pragma: no cover


def _make_mock_ws(
    *,
    budget_gate: MagicMock | None = None,
    try_claim_result: str | None = "lock-token-1",
    handle_message_fn=None,
):
    """Build a mock WebSocket with app.state pre-configured."""
    ws = AsyncMock()
    ws.app = MagicMock()

    # Session manager
    mgr = MagicMock()
    mgr.try_claim_session = AsyncMock(return_value=try_claim_result)
    mgr.load_session_from_db = AsyncMock()
    mgr.release_session = AsyncMock()
    ws.app.state.session_manager = mgr

    # Agent loop registry
    entry = MagicMock()
    entry.name = "openai"
    entry.model = "test-model"
    entry.agent_loop = MagicMock()
    entry.agent_loop.handle_message = handle_message_fn or _noop_handle_message
    registry = MagicMock()
    registry.get = MagicMock(return_value=entry)
    ws.app.state.agent_loop_registry = registry

    # Budget gate
    ws.app.state.budget_gate = budget_gate or MagicMock()

    return ws


def _approved_reservation(rid: str = "aaaa-bbbb-cccc") -> Reservation:
    return Reservation(denied=False, reservation_id=rid, reserved_eur=DEFAULT_RESERVE_EUR)


def _denied_reservation() -> Reservation:
    return Reservation(denied=True, message="Budget exceeded (test)")


def _mock_budget_gate(reservation: Reservation | None = None) -> MagicMock:
    gate = MagicMock()
    gate.try_reserve = AsyncMock(return_value=reservation or _approved_reservation())
    gate.settle = AsyncMock()
    return gate


def _sent_messages(ws: AsyncMock) -> list[dict]:
    """Parse all JSON messages sent via ws.send_text."""
    return [json.loads(call.args[0]) for call in ws.send_text.call_args_list]


# ---------------------------------------------------------------------------
# Denied path
# ---------------------------------------------------------------------------


class TestBudgetDenied:
    @pytest.mark.asyncio
    async def test_denied_returns_budget_exceeded(self):
        gate = _mock_budget_gate(_denied_reservation())
        ws = _make_mock_ws(budget_gate=gate)

        # _handle_chat_send raises GatewayError which is caught by _handle_rpc_message.
        # Calling directly: the GatewayError propagates up.
        from src.infra.errors import GatewayError

        with pytest.raises(GatewayError, match="Budget exceeded"):
            await _handle_chat_send(ws, "req-1", {"content": "hi", "session_id": "s1"})

    @pytest.mark.asyncio
    async def test_denied_does_not_call_handle_message(self):
        gate = _mock_budget_gate(_denied_reservation())
        ws = _make_mock_ws(budget_gate=gate)

        from src.infra.errors import GatewayError

        with pytest.raises(GatewayError):
            await _handle_chat_send(ws, "req-1", {"content": "hi", "session_id": "s1"})

        # _noop_handle_message is a function, not a mock. Verify deny path
        # by checking no stream chunks are sent.
        sent = _sent_messages(ws)
        stream_chunks = [m for m in sent if m.get("type") == "stream_chunk"]
        assert stream_chunks == []

    @pytest.mark.asyncio
    async def test_denied_does_not_call_settle(self):
        gate = _mock_budget_gate(_denied_reservation())
        ws = _make_mock_ws(budget_gate=gate)

        from src.infra.errors import GatewayError

        with pytest.raises(GatewayError):
            await _handle_chat_send(ws, "req-1", {"content": "hi", "session_id": "s1"})

        gate.settle.assert_not_called()


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


class TestBudgetApproved:
    @pytest.mark.asyncio
    async def test_approved_calls_settle(self):
        gate = _mock_budget_gate(_approved_reservation("rid-123"))
        ws = _make_mock_ws(budget_gate=gate)

        await _handle_chat_send(ws, "req-1", {"content": "hi", "session_id": "s1"})

        gate.settle.assert_called_once_with(
            reservation_id="rid-123",
            actual_cost_eur=DEFAULT_RESERVE_EUR,
        )

    @pytest.mark.asyncio
    async def test_handle_message_exception_still_settles(self):
        gate = _mock_budget_gate(_approved_reservation("rid-456"))
        ws = _make_mock_ws(budget_gate=gate, handle_message_fn=_failing_handle_message)

        # The RuntimeError from handle_message should propagate
        with pytest.raises(RuntimeError, match="agent_loop_exploded"):
            await _handle_chat_send(ws, "req-1", {"content": "hi", "session_id": "s1"})

        # But settle MUST still be called (finally block)
        gate.settle.assert_called_once_with(
            reservation_id="rid-456",
            actual_cost_eur=DEFAULT_RESERVE_EUR,
        )


# ---------------------------------------------------------------------------
# Settle error logging
# ---------------------------------------------------------------------------


class TestSettleErrorLogging:
    @pytest.mark.asyncio
    async def test_settle_failure_logs_required_fields(self):
        gate = _mock_budget_gate(_approved_reservation("rid-log"))
        gate.settle = AsyncMock(side_effect=RuntimeError("DB down"))
        ws = _make_mock_ws(budget_gate=gate)

        with patch("src.gateway.app.logger") as mock_logger:
            # settle fails but _handle_chat_send should NOT re-raise
            await _handle_chat_send(ws, "req-1", {"content": "hi", "session_id": "s1"})

            mock_logger.exception.assert_called_once()
            call_kwargs = mock_logger.exception.call_args[1]
            assert call_kwargs["reservation_id"] == "rid-log"
            assert call_kwargs["session_id"] == "s1"
            assert call_kwargs["provider"] == "openai"
            assert call_kwargs["model"] == "test-model"


# ---------------------------------------------------------------------------
# Parameter passthrough
# ---------------------------------------------------------------------------


class TestReserveParams:
    @pytest.mark.asyncio
    async def test_try_reserve_receives_session_id(self):
        gate = _mock_budget_gate()
        ws = _make_mock_ws(budget_gate=gate)

        await _handle_chat_send(ws, "req-1", {"content": "hi", "session_id": "my-session"})

        call_kwargs = gate.try_reserve.call_args[1]
        assert call_kwargs["session_id"] == "my-session"

    @pytest.mark.asyncio
    async def test_try_reserve_receives_eval_run_id(self):
        gate = _mock_budget_gate()
        ws = _make_mock_ws(budget_gate=gate)

        await _handle_chat_send(
            ws, "req-1",
            {"content": "hi", "session_id": "m6_eval_gemini_T10_1740000000"},
        )

        call_kwargs = gate.try_reserve.call_args[1]
        assert call_kwargs["eval_run_id"] == "m6_eval_gemini_1740000000"

    @pytest.mark.asyncio
    async def test_try_reserve_eval_run_id_empty_for_online(self):
        gate = _mock_budget_gate()
        ws = _make_mock_ws(budget_gate=gate)

        await _handle_chat_send(ws, "req-1", {"content": "hi", "session_id": "main"})

        call_kwargs = gate.try_reserve.call_args[1]
        assert call_kwargs["eval_run_id"] == ""

    @pytest.mark.asyncio
    async def test_try_reserve_receives_provider_and_model(self):
        gate = _mock_budget_gate()
        ws = _make_mock_ws(budget_gate=gate)

        await _handle_chat_send(ws, "req-1", {"content": "hi", "session_id": "s1"})

        call_kwargs = gate.try_reserve.call_args[1]
        assert call_kwargs["provider"] == "openai"
        assert call_kwargs["model"] == "test-model"
        assert call_kwargs["estimated_cost_eur"] == DEFAULT_RESERVE_EUR


# ---------------------------------------------------------------------------
# SESSION_BUSY path — no reservation
# ---------------------------------------------------------------------------


class TestSessionBusyNoReservation:
    @pytest.mark.asyncio
    async def test_session_busy_no_budget_reservation(self):
        gate = _mock_budget_gate()
        ws = _make_mock_ws(budget_gate=gate, try_claim_result=None)

        await _handle_chat_send(ws, "req-1", {"content": "hi", "session_id": "s1"})

        # Budget gate should never be touched
        gate.try_reserve.assert_not_called()
        gate.settle.assert_not_called()

        # Should have sent SESSION_BUSY
        sent = _sent_messages(ws)
        assert any(m.get("error", {}).get("code") == "SESSION_BUSY" for m in sent)
