"""Create procedure spec governance tables (P2-M2c).

Adds procedure_spec_definitions (current-state) and
procedure_spec_governance (append-only ledger) for procedure spec
governance lifecycle.

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-04-11
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op
from src.constants import DB_SCHEMA

revision = "d1e2f3a4b5c6"
down_revision = "c0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Current-state table
    op.create_table(
        "procedure_spec_definitions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
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
        schema=DB_SCHEMA,
    )

    # Governance ledger (append-only)
    op.create_table(
        "procedure_spec_governance",
        sa.Column(
            "governance_version",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("procedure_spec_id", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'proposed'"),
        ),
        sa.Column("proposal", JSONB(), nullable=False),
        sa.Column("eval_result", JSONB(), nullable=True),
        sa.Column(
            "created_by",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'agent'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "rolled_back_from",
            sa.BigInteger(),
            sa.ForeignKey(
                f"{DB_SCHEMA}.procedure_spec_governance.governance_version"
            ),
            nullable=True,
        ),
        schema=DB_SCHEMA,
    )

    # Partial unique index: single-active per spec_id
    op.execute(
        f"CREATE UNIQUE INDEX uq_procedure_spec_governance_single_active "
        f"ON {DB_SCHEMA}.procedure_spec_governance (procedure_spec_id) "
        f"WHERE status = 'active'"
    )
    op.create_index(
        "idx_procedure_spec_governance_spec_id",
        "procedure_spec_governance",
        ["procedure_spec_id"],
        schema=DB_SCHEMA,
    )
    op.create_index(
        "idx_procedure_spec_governance_status",
        "procedure_spec_governance",
        ["status"],
        schema=DB_SCHEMA,
    )


def downgrade() -> None:
    op.execute(
        f"DROP INDEX IF EXISTS {DB_SCHEMA}.uq_procedure_spec_governance_single_active"
    )
    op.drop_index(
        "idx_procedure_spec_governance_status",
        table_name="procedure_spec_governance",
        schema=DB_SCHEMA,
    )
    op.drop_index(
        "idx_procedure_spec_governance_spec_id",
        table_name="procedure_spec_governance",
        schema=DB_SCHEMA,
    )
    op.drop_table("procedure_spec_governance", schema=DB_SCHEMA)
    op.drop_table("procedure_spec_definitions", schema=DB_SCHEMA)
