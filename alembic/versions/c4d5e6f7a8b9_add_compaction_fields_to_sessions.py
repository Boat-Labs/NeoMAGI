"""add compaction fields to sessions

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-02-22

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None

SCHEMA = "neomagi"


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("compacted_context", sa.Text(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "sessions",
        sa.Column("compaction_metadata", JSONB(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "sessions",
        sa.Column("last_compaction_seq", sa.Integer(), nullable=True),
        schema=SCHEMA,
    )
    op.add_column(
        "sessions",
        sa.Column("memory_flush_candidates", JSONB(), nullable=True),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_column("sessions", "memory_flush_candidates", schema=SCHEMA)
    op.drop_column("sessions", "last_compaction_seq", schema=SCHEMA)
    op.drop_column("sessions", "compaction_metadata", schema=SCHEMA)
    op.drop_column("sessions", "compacted_context", schema=SCHEMA)
