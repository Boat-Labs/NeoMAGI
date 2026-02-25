"""Integration tests for BudgetGate with real PostgreSQL (M6 Phase 1, ADR 0041).

Tests PG atomic reserve/settle semantics, concurrent safety, and cross-provider budget.
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from src.constants import DB_SCHEMA
from src.gateway.budget_gate import BUDGET_STOP_EUR, BUDGET_WARN_EUR, BudgetGate

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def budget_gate(db_engine: AsyncEngine, db_session_factory) -> BudgetGate:
    """Create budget tables and return a BudgetGate instance.

    Budget tables are migration-managed (not ORM), so create via raw SQL.
    Depends on db_session_factory to force eager session-fixture setup
    (prevents event loop scope mismatch with autouse _integration_cleanup).
    """
    async with db_engine.begin() as conn:
        await conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {DB_SCHEMA}.budget_state (
                id TEXT PRIMARY KEY DEFAULT 'global',
                cumulative_eur NUMERIC(10,4) NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        await conn.execute(text(f"""
            CREATE TABLE IF NOT EXISTS {DB_SCHEMA}.budget_reservations (
                reservation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                eval_run_id TEXT NOT NULL DEFAULT '',
                reserved_eur NUMERIC(10,4) NOT NULL,
                actual_eur NUMERIC(10,4),
                status TEXT NOT NULL DEFAULT 'reserved',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                settled_at TIMESTAMPTZ
            )
        """))
        await conn.execute(text(f"""
            INSERT INTO {DB_SCHEMA}.budget_state (id) VALUES ('global')
            ON CONFLICT DO NOTHING
        """))

    gate = BudgetGate(db_engine, schema=DB_SCHEMA)

    yield gate

    # Cleanup
    async with db_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {DB_SCHEMA}.budget_reservations CASCADE"))
        await conn.execute(text(f"""
            UPDATE {DB_SCHEMA}.budget_state SET cumulative_eur = 0, updated_at = NOW()
            WHERE id = 'global'
        """))


class TestTryReserve:
    async def test_reserve_under_warn(self, budget_gate: BudgetGate) -> None:
        r = await budget_gate.try_reserve(
            provider="openai", model="gpt-5-mini", estimated_cost_eur=1.0,
        )
        assert not r.denied
        assert r.reservation_id
        assert r.reserved_eur == 1.0

    async def test_reserve_gemini_under_warn(self, budget_gate: BudgetGate) -> None:
        r = await budget_gate.try_reserve(
            provider="gemini", model="gemini-2.5-flash", estimated_cost_eur=2.0,
        )
        assert not r.denied

    async def test_reserve_over_warn_still_allowed(
        self, budget_gate: BudgetGate, db_engine: AsyncEngine,
    ) -> None:
        """Cumulative €20~€25 → denied=False (with warning log)."""
        # Set cumulative to €19
        async with db_engine.begin() as conn:
            await conn.execute(text(f"""
                UPDATE {DB_SCHEMA}.budget_state SET cumulative_eur = 19 WHERE id = 'global'
            """))

        r = await budget_gate.try_reserve(
            provider="openai", model="gpt-5-mini", estimated_cost_eur=2.0,
        )
        assert not r.denied
        # cumulative is now €21

    async def test_reserve_exceeds_stop(
        self, budget_gate: BudgetGate, db_engine: AsyncEngine,
    ) -> None:
        """Cumulative + estimated >= stop → denied=True."""
        async with db_engine.begin() as conn:
            await conn.execute(text(f"""
                UPDATE {DB_SCHEMA}.budget_state SET cumulative_eur = 24 WHERE id = 'global'
            """))

        r = await budget_gate.try_reserve(
            provider="openai", model="gpt-5-mini", estimated_cost_eur=2.0,
        )
        assert r.denied
        assert "Budget exceeded" in r.message

    async def test_cross_provider_shared_budget(
        self, budget_gate: BudgetGate, db_engine: AsyncEngine,
    ) -> None:
        """OpenAI + Gemini alternating reserves share global cumulative."""
        r1 = await budget_gate.try_reserve(
            provider="openai", model="gpt-5-mini", estimated_cost_eur=10.0,
        )
        assert not r1.denied

        r2 = await budget_gate.try_reserve(
            provider="gemini", model="gemini-2.5-flash", estimated_cost_eur=10.0,
        )
        assert not r2.denied

        # €20 total — next €6 should be denied (20+6 >= 25)
        r3 = await budget_gate.try_reserve(
            provider="openai", model="gpt-5-mini", estimated_cost_eur=6.0,
        )
        assert r3.denied

    async def test_session_id_recorded(
        self, budget_gate: BudgetGate, db_engine: AsyncEngine,
    ) -> None:
        r = await budget_gate.try_reserve(
            provider="openai", model="gpt-5-mini",
            estimated_cost_eur=0.01, session_id="main",
        )
        assert not r.denied

        async with db_engine.begin() as conn:
            row = await conn.execute(text(f"""
                SELECT session_id FROM {DB_SCHEMA}.budget_reservations
                WHERE reservation_id = CAST(:rid AS uuid)
            """), {"rid": r.reservation_id})
            assert row.scalar_one() == "main"

    async def test_eval_run_id_recorded(
        self, budget_gate: BudgetGate, db_engine: AsyncEngine,
    ) -> None:
        r = await budget_gate.try_reserve(
            provider="gemini", model="gemini-2.5-flash",
            estimated_cost_eur=0.01, eval_run_id="m6_eval_gemini_1740000000",
        )
        assert not r.denied

        async with db_engine.begin() as conn:
            row = await conn.execute(text(f"""
                SELECT eval_run_id FROM {DB_SCHEMA}.budget_reservations
                WHERE reservation_id = CAST(:rid AS uuid)
            """), {"rid": r.reservation_id})
            assert row.scalar_one() == "m6_eval_gemini_1740000000"

    async def test_eval_run_id_empty_for_online(
        self, budget_gate: BudgetGate, db_engine: AsyncEngine,
    ) -> None:
        r = await budget_gate.try_reserve(
            provider="openai", model="gpt-5-mini",
            estimated_cost_eur=0.01, eval_run_id="",
        )
        async with db_engine.begin() as conn:
            row = await conn.execute(text(f"""
                SELECT eval_run_id FROM {DB_SCHEMA}.budget_reservations
                WHERE reservation_id = CAST(:rid AS uuid)
            """), {"rid": r.reservation_id})
            assert row.scalar_one() == ""

    async def test_provider_breakdown_queryable(
        self, budget_gate: BudgetGate, db_engine: AsyncEngine,
    ) -> None:
        """Reservations per provider can be queried."""
        await budget_gate.try_reserve(
            provider="openai", model="gpt-5-mini", estimated_cost_eur=1.0,
        )
        await budget_gate.try_reserve(
            provider="gemini", model="gemini-2.5-flash", estimated_cost_eur=2.0,
        )
        await budget_gate.try_reserve(
            provider="openai", model="gpt-5-mini", estimated_cost_eur=3.0,
        )

        async with db_engine.begin() as conn:
            row = await conn.execute(text(f"""
                SELECT provider, SUM(reserved_eur) as total
                FROM {DB_SCHEMA}.budget_reservations
                GROUP BY provider ORDER BY provider
            """))
            results = {r[0]: float(r[1]) for r in row.fetchall()}
            assert results["gemini"] == 2.0
            assert results["openai"] == 4.0

    async def test_eval_run_id_filter(
        self, budget_gate: BudgetGate, db_engine: AsyncEngine,
    ) -> None:
        """Filter by eval_run_id returns only that run's records."""
        run_id = "m6_eval_gemini_1740000000"
        await budget_gate.try_reserve(
            provider="gemini", model="gemini-2.5-flash",
            estimated_cost_eur=1.0, eval_run_id=run_id,
        )
        await budget_gate.try_reserve(
            provider="openai", model="gpt-5-mini",
            estimated_cost_eur=2.0, eval_run_id="other_run",
        )

        async with db_engine.begin() as conn:
            row = await conn.execute(text(f"""
                SELECT COUNT(*) FROM {DB_SCHEMA}.budget_reservations
                WHERE eval_run_id = :run_id
            """), {"run_id": run_id})
            assert row.scalar_one() == 1


class TestSettle:
    async def test_settle_actual_less_than_reserved(
        self, budget_gate: BudgetGate, db_engine: AsyncEngine,
    ) -> None:
        """Settle with actual < reserved → cumulative decreases."""
        r = await budget_gate.try_reserve(
            provider="openai", model="gpt-5-mini", estimated_cost_eur=5.0,
        )
        await budget_gate.settle(reservation_id=r.reservation_id, actual_cost_eur=3.0)

        async with db_engine.begin() as conn:
            row = await conn.execute(text(f"""
                SELECT cumulative_eur FROM {DB_SCHEMA}.budget_state WHERE id = 'global'
            """))
            # 5.0 reserved, then diff = 3.0 - 5.0 = -2.0 → cumulative = 3.0
            assert float(row.scalar_one()) == 3.0

    async def test_settle_actual_more_than_reserved(
        self, budget_gate: BudgetGate, db_engine: AsyncEngine,
    ) -> None:
        """Settle with actual > reserved → cumulative increases."""
        r = await budget_gate.try_reserve(
            provider="openai", model="gpt-5-mini", estimated_cost_eur=3.0,
        )
        await budget_gate.settle(reservation_id=r.reservation_id, actual_cost_eur=5.0)

        async with db_engine.begin() as conn:
            row = await conn.execute(text(f"""
                SELECT cumulative_eur FROM {DB_SCHEMA}.budget_state WHERE id = 'global'
            """))
            # 3.0 reserved, then diff = 5.0 - 3.0 = +2.0 → cumulative = 5.0
            assert float(row.scalar_one()) == 5.0

    async def test_settle_idempotent(
        self, budget_gate: BudgetGate, db_engine: AsyncEngine,
    ) -> None:
        """Duplicate settle is a no-op: cumulative doesn't change."""
        r = await budget_gate.try_reserve(
            provider="openai", model="gpt-5-mini", estimated_cost_eur=5.0,
        )
        await budget_gate.settle(reservation_id=r.reservation_id, actual_cost_eur=3.0)

        async with db_engine.begin() as conn:
            row = await conn.execute(text(f"""
                SELECT cumulative_eur FROM {DB_SCHEMA}.budget_state WHERE id = 'global'
            """))
            before = float(row.scalar_one())

        # Second settle — should be no-op
        await budget_gate.settle(reservation_id=r.reservation_id, actual_cost_eur=10.0)

        async with db_engine.begin() as conn:
            row = await conn.execute(text(f"""
                SELECT cumulative_eur FROM {DB_SCHEMA}.budget_state WHERE id = 'global'
            """))
            after = float(row.scalar_one())

        assert before == after == 3.0

    async def test_settle_unknown_reservation_noop(
        self, budget_gate: BudgetGate, db_engine: AsyncEngine,
    ) -> None:
        """Settle with unknown reservation_id is a no-op."""
        # Get initial cumulative
        async with db_engine.begin() as conn:
            row = await conn.execute(text(f"""
                SELECT cumulative_eur FROM {DB_SCHEMA}.budget_state WHERE id = 'global'
            """))
            before = float(row.scalar_one())

        await budget_gate.settle(
            reservation_id="00000000-0000-0000-0000-000000000000",
            actual_cost_eur=999.0,
        )

        async with db_engine.begin() as conn:
            row = await conn.execute(text(f"""
                SELECT cumulative_eur FROM {DB_SCHEMA}.budget_state WHERE id = 'global'
            """))
            after = float(row.scalar_one())

        assert before == after


class TestConcurrency:
    @pytest.mark.pg_required
    async def test_concurrent_reserves_no_oversell(
        self, budget_gate: BudgetGate, db_engine: AsyncEngine,
    ) -> None:
        """Two concurrent reserves when only room for one → only one succeeds."""
        # Set cumulative to €22 — room for one €2 reserve (22+2=24 < 25), not two (24+2=26 >= 25)
        async with db_engine.begin() as conn:
            await conn.execute(text(f"""
                UPDATE {DB_SCHEMA}.budget_state SET cumulative_eur = 22 WHERE id = 'global'
            """))

        results = await asyncio.gather(
            budget_gate.try_reserve(
                provider="openai", model="gpt-5-mini", estimated_cost_eur=2.0,
            ),
            budget_gate.try_reserve(
                provider="gemini", model="gemini-2.5-flash", estimated_cost_eur=2.0,
            ),
        )

        granted = [r for r in results if not r.denied]
        denied = [r for r in results if r.denied]
        # PG serializes: exactly one should succeed
        assert len(granted) == 1
        assert len(denied) == 1

    @pytest.mark.pg_required
    async def test_multi_worker_concurrent_reserves(
        self, budget_gate: BudgetGate, db_engine: AsyncEngine,
    ) -> None:
        """Simulate multi-worker concurrent reserves — cumulative stays consistent."""
        tasks = [
            budget_gate.try_reserve(
                provider="openai", model="gpt-5-mini", estimated_cost_eur=1.0,
            )
            for _ in range(10)
        ]
        results = await asyncio.gather(*tasks)
        granted = [r for r in results if not r.denied]

        # Verify cumulative matches number of grants
        async with db_engine.begin() as conn:
            row = await conn.execute(text(f"""
                SELECT cumulative_eur FROM {DB_SCHEMA}.budget_state WHERE id = 'global'
            """))
            cumulative = float(row.scalar_one())

        assert cumulative == len(granted) * 1.0
