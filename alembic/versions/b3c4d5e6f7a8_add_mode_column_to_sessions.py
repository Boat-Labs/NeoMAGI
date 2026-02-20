"""add mode column to sessions

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-02-21 00:00:00.000000

M1.5 Tool Modes: session-level mode field.
Default 'chat_safe' ensures all existing sessions are safe.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b3c4d5e6f7a8"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column(
            "mode",
            sa.String(length=16),
            nullable=False,
            server_default="chat_safe",
        ),
        schema="neomagi",
    )


def downgrade() -> None:
    op.drop_column("sessions", "mode", schema="neomagi")
