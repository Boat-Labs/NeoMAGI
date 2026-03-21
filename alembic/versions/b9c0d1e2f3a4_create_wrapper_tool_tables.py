"""Create wrapper_tools and wrapper_tool_versions tables (P2-M1c).

Current-state store (wrapper_tools) is separated from the governance
ledger (wrapper_tool_versions).  wrapper_tool_versions.wrapper_tool_id
is intentionally NOT a FK to wrapper_tools.id because a proposal may
reference a wrapper tool that has not yet been materialized.

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-03-19
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op
from src.constants import DB_SCHEMA

revision = "b9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- current-state: wrapper_tools --
    op.create_table(
        "wrapper_tools",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("input_schema", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("output_schema", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column(
            "bound_atomic_tools",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("implementation_ref", sa.Text(), nullable=False),
        sa.Column(
            "deny_semantics",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "scope_claim",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'local'"),
        ),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
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

    # -- governance ledger: wrapper_tool_versions --
    # wrapper_tool_id is NOT a FK to wrapper_tools.id (see docstring)
    op.create_table(
        "wrapper_tool_versions",
        sa.Column(
            "governance_version",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("wrapper_tool_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'proposed'")),
        sa.Column("proposal", JSONB(), nullable=False),
        sa.Column("eval_result", JSONB(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False, server_default=sa.text("'agent'")),
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
                f"{DB_SCHEMA}.wrapper_tool_versions.governance_version"
            ),
            nullable=True,
        ),
        schema=DB_SCHEMA,
    )
    op.create_index(
        "idx_wrapper_tool_versions_wrapper_tool_id",
        "wrapper_tool_versions",
        ["wrapper_tool_id"],
        schema=DB_SCHEMA,
    )
    op.create_index(
        "idx_wrapper_tool_versions_status",
        "wrapper_tool_versions",
        ["status"],
        schema=DB_SCHEMA,
    )
    # Single-active invariant: at most one active version per wrapper_tool_id
    op.execute(
        f"CREATE UNIQUE INDEX uq_wrapper_tool_versions_single_active "
        f"ON {DB_SCHEMA}.wrapper_tool_versions (wrapper_tool_id) "
        f"WHERE status = 'active'"
    )


def downgrade() -> None:
    op.execute(
        f"DROP INDEX IF EXISTS {DB_SCHEMA}.uq_wrapper_tool_versions_single_active"
    )
    op.drop_index(
        "idx_wrapper_tool_versions_status",
        table_name="wrapper_tool_versions",
        schema=DB_SCHEMA,
    )
    op.drop_index(
        "idx_wrapper_tool_versions_wrapper_tool_id",
        table_name="wrapper_tool_versions",
        schema=DB_SCHEMA,
    )
    op.drop_table("wrapper_tool_versions", schema=DB_SCHEMA)
    op.drop_table("wrapper_tools", schema=DB_SCHEMA)
