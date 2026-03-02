"""Tests for dispatch_chat (P1: core dispatch extraction).

Covers:
- Normal dispatch: events yielded from handle_message
- SESSION_BUSY: raises GatewayError
- BUDGET_EXCEEDED: raises GatewayError
- session_id passthrough (not scope-resolved)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.agent.events import TextChunk
from src.gateway.budget_gate import Reservation
from src.gateway.dispatch import DEFAULT_RESERVE_EUR, dispatch_chat
from src.infra.errors import GatewayError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _text_handle_message(*_args, **_kwargs):
    """Async generator yielding a single TextChunk."""
    yield TextChunk(content="hello")


async def _noop_handle_message(*_args, **_kwargs):
    """Empty async generator."""
    return
    yield  # pragma: no cover


def _approved_reservation(rid: str = "res-1") -> Reservation:
    return Reservation(denied=False, reservation_id=rid, reserved_eur=DEFAULT_RESERVE_EUR)


def _denied_reservation() -> Reservation:
    return Reservation(denied=True, message="Budget exceeded (test)")


def _make_deps(
    *,
    try_claim_result: str | None = "lock-1",
    reservation: Reservation | None = None,
    handle_message_fn=None,
):
    """Build mock dependencies for dispatch_chat."""
    entry = MagicMock()
    entry.name = "openai"
    entry.model = "test-model"
    entry.agent_loop = MagicMock()
    entry.agent_loop.handle_message = handle_message_fn or _noop_handle_message

    registry = MagicMock()
    registry.get = MagicMock(return_value=entry)

    mgr = MagicMock()
    mgr.try_claim_session = AsyncMock(return_value=try_claim_result)
    mgr.load_session_from_db = AsyncMock()
    mgr.release_session = AsyncMock()

    gate = MagicMock()
    gate.try_reserve = AsyncMock(return_value=reservation or _approved_reservation())
    gate.settle = AsyncMock()

    return registry, mgr, gate, entry


# ---------------------------------------------------------------------------
# Normal dispatch
# ---------------------------------------------------------------------------


class TestNormalDispatch:
    @pytest.mark.asyncio
    async def test_yields_events_from_handle_message(self):
        registry, mgr, gate, entry = _make_deps(
            handle_message_fn=_text_handle_message,
        )

        events = []
        async for event in dispatch_chat(
            registry=registry,
            session_manager=mgr,
            budget_gate=gate,
            session_id="s1",
            content="hi",
        ):
            events.append(event)

        assert len(events) == 1
        assert isinstance(events[0], TextChunk)
        assert events[0].content == "hello"

    @pytest.mark.asyncio
    async def test_settle_called_on_success(self):
        registry, mgr, gate, _ = _make_deps(
            reservation=_approved_reservation("rid-ok"),
        )

        async for _ in dispatch_chat(
            registry=registry,
            session_manager=mgr,
            budget_gate=gate,
            session_id="s1",
            content="hi",
        ):
            pass

        gate.settle.assert_called_once_with(
            reservation_id="rid-ok",
            actual_cost_eur=DEFAULT_RESERVE_EUR,
        )

    @pytest.mark.asyncio
    async def test_release_called_on_success(self):
        registry, mgr, gate, _ = _make_deps()

        async for _ in dispatch_chat(
            registry=registry,
            session_manager=mgr,
            budget_gate=gate,
            session_id="s1",
            content="hi",
        ):
            pass

        mgr.release_session.assert_called_once_with("s1", "lock-1")


# ---------------------------------------------------------------------------
# SESSION_BUSY
# ---------------------------------------------------------------------------


class TestSessionBusy:
    @pytest.mark.asyncio
    async def test_session_busy_raises_gateway_error(self):
        registry, mgr, gate, _ = _make_deps(try_claim_result=None)

        with pytest.raises(GatewayError) as exc_info:
            async for _ in dispatch_chat(
                registry=registry,
                session_manager=mgr,
                budget_gate=gate,
                session_id="s1",
                content="hi",
            ):
                pass  # pragma: no cover

        assert exc_info.value.code == "SESSION_BUSY"
        gate.try_reserve.assert_not_called()


# ---------------------------------------------------------------------------
# BUDGET_EXCEEDED
# ---------------------------------------------------------------------------


class TestBudgetExceeded:
    @pytest.mark.asyncio
    async def test_budget_exceeded_raises_gateway_error(self):
        registry, mgr, gate, _ = _make_deps(reservation=_denied_reservation())

        with pytest.raises(GatewayError) as exc_info:
            async for _ in dispatch_chat(
                registry=registry,
                session_manager=mgr,
                budget_gate=gate,
                session_id="s1",
                content="hi",
            ):
                pass  # pragma: no cover

        assert exc_info.value.code == "BUDGET_EXCEEDED"
        gate.settle.assert_not_called()


# ---------------------------------------------------------------------------
# session_id passthrough
# ---------------------------------------------------------------------------


class TestSessionIdPassthrough:
    @pytest.mark.asyncio
    async def test_session_id_passed_to_claim(self):
        registry, mgr, gate, _ = _make_deps()

        async for _ in dispatch_chat(
            registry=registry,
            session_manager=mgr,
            budget_gate=gate,
            session_id="custom-session",
            content="hi",
        ):
            pass

        mgr.try_claim_session.assert_called_once_with(
            "custom-session", ttl_seconds=300,
        )

    @pytest.mark.asyncio
    async def test_session_id_passed_to_handle_message(self):
        registry, mgr, gate, entry = _make_deps()
        entry.agent_loop.handle_message = _noop_handle_message

        # Use a spy to capture call args
        call_args_list = []
        original_fn = _noop_handle_message

        async def spy(*args, **kwargs):
            call_args_list.append(kwargs)
            async for event in original_fn(*args, **kwargs):
                yield event  # pragma: no cover

        entry.agent_loop.handle_message = spy

        async for _ in dispatch_chat(
            registry=registry,
            session_manager=mgr,
            budget_gate=gate,
            session_id="my-sid",
            content="hello",
        ):
            pass

        assert call_args_list[0]["session_id"] == "my-sid"

    @pytest.mark.asyncio
    async def test_identity_and_dm_scope_passed_to_handle_message(self):
        """dispatch_chat passes identity and dm_scope to handle_message."""
        from src.session.scope_resolver import SessionIdentity

        registry, mgr, gate, entry = _make_deps()

        call_kwargs_list = []

        async def spy(*args, **kwargs):
            call_kwargs_list.append(kwargs)
            return
            yield  # pragma: no cover

        entry.agent_loop.handle_message = spy

        identity = SessionIdentity(
            session_id="tg-s1", channel_type="telegram", peer_id="789"
        )

        async for _ in dispatch_chat(
            registry=registry,
            session_manager=mgr,
            budget_gate=gate,
            session_id="tg-s1",
            content="hi",
            identity=identity,
            dm_scope="per-channel-peer",
        ):
            pass

        assert call_kwargs_list[0]["identity"] is identity
        assert call_kwargs_list[0]["dm_scope"] == "per-channel-peer"
