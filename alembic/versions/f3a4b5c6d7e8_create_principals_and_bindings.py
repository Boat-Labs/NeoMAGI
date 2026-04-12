"""Create principals and principal_bindings tables, add sessions.principal_id (P2-M3a).

Adds canonical principal identity model with:
- principals table (single-owner partial unique index)
- principal_bindings table (channel identity → principal mapping)
- sessions.principal_id FK for session ownership

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-04-12
"""

import sqlalchemy as sa

from alembic import op
from src.constants import DB_SCHEMA

revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- principals ---
    op.create_table(
        "principals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("password_hash", sa.String(256), nullable=True),
        sa.Column("role", sa.String(16), nullable=False, server_default="owner"),
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
    op.execute(
        f"CREATE UNIQUE INDEX uq_principals_single_owner "
        f"ON {DB_SCHEMA}.principals (role) "
        f"WHERE role = 'owner'"
    )

    # --- principal_bindings ---
    op.create_table(
        "principal_bindings",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "principal_id",
            sa.String(36),
            sa.ForeignKey(f"{DB_SCHEMA}.principals.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("channel_type", sa.String(32), nullable=False),
        sa.Column("channel_identity", sa.String(256), nullable=False),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
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
    op.create_unique_constraint(
        "uq_principal_bindings_channel",
        "principal_bindings",
        ["channel_type", "channel_identity"],
        schema=DB_SCHEMA,
    )
    op.create_index(
        "idx_principal_bindings_principal",
        "principal_bindings",
        ["principal_id"],
        schema=DB_SCHEMA,
    )

    # --- sessions.principal_id ---
    op.add_column(
        "sessions",
        sa.Column(
            "principal_id",
            sa.String(36),
            sa.ForeignKey(f"{DB_SCHEMA}.principals.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        schema=DB_SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("sessions", "principal_id", schema=DB_SCHEMA)
    op.drop_table("principal_bindings", schema=DB_SCHEMA)
    op.drop_table("principals", schema=DB_SCHEMA)
