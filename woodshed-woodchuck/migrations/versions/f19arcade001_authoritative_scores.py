"""Add independently validated scores and server-owned Arcade challenge state.

No legacy browser score is promoted. NULL retains its unverified meaning.
"""
from alembic import op
import sqlalchemy as sa

revision = 'f19arcade001'
down_revision = 'e18tester001'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('arcade_play_sessions', sa.Column('authoritative_score', sa.Integer(), nullable=True))
    op.add_column('arcade_play_sessions', sa.Column('challenge_state', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('arcade_play_sessions', 'challenge_state')
    op.drop_column('arcade_play_sessions', 'authoritative_score')
