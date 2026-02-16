"""create_sessions_and_messages_tables

Revision ID: f2d8d48c9ef1
Revises:
Create Date: 2026-02-16 20:58:54.271970

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f2d8d48c9ef1'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('sessions',
    sa.Column('id', sa.String(length=128), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    schema='neomagi'
    )
    op.create_table('messages',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('session_id', sa.String(length=128), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('tool_calls', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('tool_call_id', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['neomagi.sessions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    schema='neomagi'
    )
    op.create_index(op.f('ix_neomagi_messages_session_id'), 'messages', ['session_id'], unique=False, schema='neomagi')


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_neomagi_messages_session_id'), table_name='messages', schema='neomagi')
    op.drop_table('messages', schema='neomagi')
    op.drop_table('sessions', schema='neomagi')
