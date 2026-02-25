"""All-provider budget gate with PostgreSQL atomic semantics (ADR 0041).

Multi-worker safe: uses PG row-level locking, not in-memory locks.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = structlog.get_logger()

BUDGET_WARN_EUR: float = 20.0
BUDGET_STOP_EUR: float = 25.0


@dataclass
class Reservation:
    denied: bool
    message: str = ""
    reservation_id: str = ""
    reserved_eur: float = 0.0


class BudgetGate:
    """All-provider budget gate with PostgreSQL atomic semantics (ADR 0041).

    Multi-worker safe: uses PG row-level locking, not in-memory locks.
    Uses SQLAlchemy AsyncEngine (consistent with project infrastructure).
    """

    def __init__(self, engine: AsyncEngine, *, schema: str = "neomagi") -> None:
        self._engine = engine
        self._schema = schema

    async def try_reserve(
        self,
        *,
        provider: str,
        model: str,
        estimated_cost_eur: float,
        session_id: str = "",
        eval_run_id: str = "",
    ) -> Reservation:
        """Atomic: check global budget + reserve estimated cost in one PG transaction.

        PG READ COMMITTED + row-level lock on budget_state ensures
        concurrent requests are serialized and cannot oversell.
        """
        async with self._engine.begin() as conn:
            # Atomic: UPDATE with WHERE guard; PG row lock serializes concurrent attempts
            row = await conn.execute(
                text(f"""
                    UPDATE {self._schema}.budget_state
                    SET cumulative_eur = cumulative_eur + :cost, updated_at = NOW()
                    WHERE id = 'global'
                      AND cumulative_eur + :cost < :stop
                    RETURNING cumulative_eur
                """),
                {"cost": Decimal(str(estimated_cost_eur)), "stop": Decimal(str(BUDGET_STOP_EUR))},
            )
            result = row.fetchone()

            if result is None:
                # Budget exceeded — read current for error message
                current_row = await conn.execute(text(
                    f"SELECT cumulative_eur FROM {self._schema}.budget_state"
                    " WHERE id = 'global'"
                ))
                current = float(current_row.scalar_one())
                return Reservation(
                    denied=True,
                    message=(
                        f"Budget exceeded (cumulative €{current:.2f} "
                        f"+ estimated €{estimated_cost_eur:.2f} "
                        f">= stop €{BUDGET_STOP_EUR})."
                    ),
                )

            # Record reservation
            rid_row = await conn.execute(
                text(f"""
                    INSERT INTO {self._schema}.budget_reservations
                        (provider, model, session_id, eval_run_id, reserved_eur, status)
                    VALUES (:provider, :model, :session_id, :eval_run_id, :reserved_eur, 'reserved')
                    RETURNING reservation_id
                """),
                {
                    "provider": provider,
                    "model": model,
                    "session_id": session_id,
                    "eval_run_id": eval_run_id,
                    "reserved_eur": Decimal(str(estimated_cost_eur)),
                },
            )
            rid = str(rid_row.scalar_one())

            cumulative = float(result[0])
            if cumulative >= BUDGET_WARN_EUR:
                logger.warning("budget_warning", cumulative_eur=cumulative, provider=provider)

            return Reservation(
                denied=False,
                reservation_id=rid,
                reserved_eur=estimated_cost_eur,
            )

    async def settle(
        self,
        *,
        reservation_id: str,
        actual_cost_eur: float,
    ) -> None:
        """Idempotent post-call reconciliation: CAS flip reservation first,
        only adjust budget_state if flip succeeds. Duplicate settle is a no-op.
        """
        async with self._engine.begin() as conn:
            # CAS: atomically flip reserved → settled; returns row only on first call
            settled_row = await conn.execute(
                text(f"""
                    UPDATE {self._schema}.budget_reservations
                    SET actual_eur = :actual, status = 'settled', settled_at = NOW()
                    WHERE reservation_id = CAST(:rid AS uuid) AND status = 'reserved'
                    RETURNING reserved_eur
                """),
                {"actual": Decimal(str(actual_cost_eur)), "rid": reservation_id},
            )
            settled_result = settled_row.fetchone()

            if settled_result is None:
                # Already settled or unknown — idempotent no-op
                return

            # Only adjust cumulative when CAS succeeded
            diff = Decimal(str(actual_cost_eur)) - settled_result[0]
            await conn.execute(
                text(f"""
                    UPDATE {self._schema}.budget_state
                    SET cumulative_eur = cumulative_eur + :diff, updated_at = NOW()
                    WHERE id = 'global'
                """),
                {"diff": diff},
            )
