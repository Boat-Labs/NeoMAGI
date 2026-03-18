"""Create skill_specs, skill_evidence, skill_spec_versions tables (P2-M1b).

Current-state store (skill_specs + skill_evidence) is separated from the
governance ledger (skill_spec_versions).  skill_spec_versions.skill_id is
intentionally NOT a FK to skill_specs.id because a proposal may reference
a skill that has not yet been materialized.

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-03-18
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op
from src.constants import DB_SCHEMA

revision = "a8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- current-state: skill_specs --
    op.create_table(
        "skill_specs",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("activation", sa.Text(), nullable=False),
        sa.Column("activation_tags", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("preconditions", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("delta", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("tool_preferences", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("escalation_rules", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("exchange_policy", sa.Text(), nullable=False, server_default="local_only"),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default="false"),
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

    # -- current-state: skill_evidence --
    op.create_table(
        "skill_evidence",
        sa.Column(
            "skill_id",
            sa.Text(),
            sa.ForeignKey(f"{DB_SCHEMA}.skill_specs.id"),
            primary_key=True,
        ),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("positive_patterns", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("negative_patterns", JSONB(), nullable=False, server_default="'[]'"),
        sa.Column("known_breakages", JSONB(), nullable=False, server_default="'[]'"),
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

    # -- governance ledger: skill_spec_versions --
    # skill_id is NOT a FK to skill_specs.id (see docstring)
    op.create_table(
        "skill_spec_versions",
        sa.Column(
            "governance_version",
            sa.BigInteger(),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("skill_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="proposed"),
        sa.Column("proposal", JSONB(), nullable=False),
        sa.Column("eval_result", JSONB(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False, server_default="agent"),
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
            sa.ForeignKey(f"{DB_SCHEMA}.skill_spec_versions.governance_version"),
            nullable=True,
        ),
        schema=DB_SCHEMA,
    )
    op.create_index(
        "idx_skill_spec_versions_skill_id",
        "skill_spec_versions",
        ["skill_id"],
        schema=DB_SCHEMA,
    )
    op.create_index(
        "idx_skill_spec_versions_status",
        "skill_spec_versions",
        ["status"],
        schema=DB_SCHEMA,
    )


def downgrade() -> None:
    # Drop in dependency-reverse order
    op.drop_index(
        "idx_skill_spec_versions_status",
        table_name="skill_spec_versions",
        schema=DB_SCHEMA,
    )
    op.drop_index(
        "idx_skill_spec_versions_skill_id",
        table_name="skill_spec_versions",
        schema=DB_SCHEMA,
    )
    op.drop_table("skill_spec_versions", schema=DB_SCHEMA)
    op.drop_table("skill_evidence", schema=DB_SCHEMA)
    op.drop_table("skill_specs", schema=DB_SCHEMA)
