"""add session seq/lock columns and message unique constraint

Revision ID: a1b2c3d4e5f6
Revises: f2d8d48c9ef1
Create Date: 2026-02-18 10:00:00.000000

[Decision 0021] Multi-worker session ordering and no-silent-drop:
- next_seq: atomic DB-level seq allocation
- lock_token + processing_since: session-level lease lock
- uq_messages_session_seq: safety net for seq uniqueness
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f2d8d48c9ef1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- sessions: add next_seq, lock_token, processing_since ---
    op.add_column(
        "sessions",
        sa.Column("next_seq", sa.Integer(), nullable=False, server_default="0"),
        schema="neomagi",
    )
    op.add_column(
        "sessions",
        sa.Column("lock_token", sa.String(length=36), nullable=True),
        schema="neomagi",
    )
    op.add_column(
        "sessions",
        sa.Column("processing_since", sa.DateTime(timezone=True), nullable=True),
        schema="neomagi",
    )

    # Backfill next_seq from existing messages
    op.execute(
        """
        UPDATE neomagi.sessions
        SET next_seq = (
            SELECT COALESCE(MAX(seq), -1) + 1
            FROM neomagi.messages
            WHERE messages.session_id = sessions.id
        )
        """
    )

    # --- messages: deduplicate before adding UNIQUE constraint ---
    # Old Python-side seq allocation could produce duplicates under race conditions.
    # Keep the row with the smallest id (earliest insert) and delete later duplicates.
    op.execute(
        """
        DELETE FROM neomagi.messages
        WHERE id IN (
            SELECT id FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY session_id, seq
                           ORDER BY id
                       ) AS rn
                FROM neomagi.messages
            ) ranked
            WHERE rn > 1
        )
        """
    )

    # --- messages: add UNIQUE constraint on (session_id, seq) ---
    op.create_unique_constraint(
        "uq_messages_session_seq",
        "messages",
        ["session_id", "seq"],
        schema="neomagi",
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_messages_session_seq", "messages", schema="neomagi", type_="unique"
    )
    op.drop_column("sessions", "processing_since", schema="neomagi")
    op.drop_column("sessions", "lock_token", schema="neomagi")
    op.drop_column("sessions", "next_seq", schema="neomagi")
