"""Three-attempt Arcade purchases and durable start retries; retain legacy plays."""
from alembic import op
import sqlalchemy as sa

revision = 'c15arcade001'
down_revision = 'b14tester001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('arcade_attempt_packs',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('woodchuck_profiles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('game_key', sa.String(30), nullable=False),
        sa.Column('cost', sa.Integer(), nullable=False),
        sa.Column('attempts_used', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('cost = 100', name='ck_arcade_pack_cost'),
        sa.CheckConstraint('attempts_used BETWEEN 0 AND 3', name='ck_arcade_pack_attempts'))
    op.create_index('ix_arcade_packs_profile_game', 'arcade_attempt_packs', ['profile_id', 'game_key'])
    with op.batch_alter_table('arcade_play_sessions') as batch:
        batch.drop_constraint('ck_arcade_play_session_entry_cost', type_='check')
        batch.create_check_constraint('ck_arcade_play_session_entry_cost', 'entry_cost IN (0, 1, 100)')
        batch.alter_column('entry_cost', server_default='0', existing_type=sa.Integer())
        batch.add_column(sa.Column('pack_id', sa.Integer(), nullable=True))
        batch.add_column(sa.Column('attempt_number', sa.Integer(), nullable=True))
        batch.create_foreign_key('fk_arcade_play_pack', 'arcade_attempt_packs', ['pack_id'], ['id'])
        batch.create_unique_constraint('uq_arcade_pack_attempt', ['pack_id', 'attempt_number'])
        batch.create_check_constraint('ck_arcade_pack_attempt_number',
            '(pack_id IS NULL AND attempt_number IS NULL) OR (pack_id IS NOT NULL AND attempt_number IS NOT NULL AND attempt_number BETWEEN 1 AND 3)')
    op.create_table('arcade_start_requests',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('woodchuck_profiles.id', ondelete='CASCADE'), nullable=False),
        sa.Column('request_id', sa.String(64), nullable=False),
        sa.Column('play_id', sa.Integer(), sa.ForeignKey('arcade_play_sessions.id', ondelete='CASCADE'), nullable=False),
        sa.UniqueConstraint('profile_id', 'request_id', name='uq_arcade_start_request'))
    op.create_index('ix_arcade_play_game_completed', 'arcade_play_sessions', ['game_key', 'completed_at'])


def downgrade():
    # Old code cannot represent packs/free plays or durable retries, even for legacy plays.
    # Refuse before any schema/data mutation so purchase and retry history remain intact.
    if op.get_bind().scalar(sa.text('SELECT COUNT(*) FROM arcade_attempt_packs')) or op.get_bind().scalar(
            sa.text('SELECT COUNT(*) FROM arcade_play_sessions WHERE entry_cost <> 1')) or op.get_bind().scalar(
            sa.text('SELECT COUNT(*) FROM arcade_start_requests')):
        raise RuntimeError('Arcade R3 activity exists; use a forward fix, not a lossy downgrade.')
    op.drop_table('arcade_start_requests')
    op.drop_index('ix_arcade_play_game_completed', table_name='arcade_play_sessions')
    with op.batch_alter_table('arcade_play_sessions') as batch:
        batch.drop_constraint('uq_arcade_pack_attempt', type_='unique')
        batch.drop_constraint('ck_arcade_pack_attempt_number', type_='check')
        batch.drop_constraint('fk_arcade_play_pack', type_='foreignkey')
        batch.drop_column('attempt_number')
        batch.drop_column('pack_id')
        batch.drop_constraint('ck_arcade_play_session_entry_cost', type_='check')
        batch.create_check_constraint('ck_arcade_play_session_entry_cost', 'entry_cost = 1')
        batch.alter_column('entry_cost', server_default='1', existing_type=sa.Integer())
    op.drop_index('ix_arcade_packs_profile_game', table_name='arcade_attempt_packs')
    op.drop_table('arcade_attempt_packs')
