"""Create active_procedures table (P2-M2a).

Stores runtime state for active procedure instances.
Uses a partial unique index on (session_id) WHERE completed_at IS NULL
to enforce single-active-per-session invariant.

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-04-07
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op
from src.constants import DB_SCHEMA

revision = "c0d1e2f3a4b5"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "active_procedures",
        sa.Column("instance_id", sa.Text(), primary_key=True),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("spec_id", sa.Text(), nullable=False),
        sa.Column("spec_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("context", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "execution_metadata",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        schema=DB_SCHEMA,
    )
    # Single-active-per-session: at most one non-completed procedure per session
    op.execute(
        f"CREATE UNIQUE INDEX uq_active_procedures_session_single_active "
        f"ON {DB_SCHEMA}.active_procedures (session_id) "
        f"WHERE completed_at IS NULL"
    )
    op.create_index(
        "idx_active_procedures_session_id",
        "active_procedures",
        ["session_id"],
        schema=DB_SCHEMA,
    )


def downgrade() -> None:
    op.execute(
        f"DROP INDEX IF EXISTS {DB_SCHEMA}.uq_active_procedures_session_single_active"
    )
    op.drop_index(
        "idx_active_procedures_session_id",
        table_name="active_procedures",
        schema=DB_SCHEMA,
    )
    op.drop_table("active_procedures", schema=DB_SCHEMA)
