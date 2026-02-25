"""Add budget_state and budget_reservations tables for M6 cost governance.

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-02-25
"""

from alembic import op
import sqlalchemy as sa

from src.constants import DB_SCHEMA

revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"


def upgrade() -> None:
    op.create_table(
        "budget_state",
        sa.Column("id", sa.Text(), primary_key=True, server_default="global"),
        sa.Column(
            "cumulative_eur",
            sa.Numeric(precision=10, scale=4),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        schema=DB_SCHEMA,
    )
    # Seed global row
    op.execute(
        f"INSERT INTO {DB_SCHEMA}.budget_state (id) VALUES ('global') ON CONFLICT DO NOTHING"
    )

    op.create_table(
        "budget_reservations",
        sa.Column(
            "reservation_id",
            sa.UUID(),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("eval_run_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("reserved_eur", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("actual_eur", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="reserved"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        schema=DB_SCHEMA,
    )
    op.create_index(
        "idx_budget_reservations_status",
        "budget_reservations",
        ["status"],
        schema=DB_SCHEMA,
        postgresql_where=sa.text("status = 'reserved'"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_budget_reservations_status",
        table_name="budget_reservations",
        schema=DB_SCHEMA,
    )
    op.drop_table("budget_reservations", schema=DB_SCHEMA)
    op.drop_table("budget_state", schema=DB_SCHEMA)
