"""Age screen without cohorts, consent or inferred age backfill.
Revision ID: a13screen001; Revises: v2r3s4t5u6v7
"""
from alembic import op
import sqlalchemy as sa
revision = 'a13screen001'
down_revision = 'v2r3s4t5u6v7'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('account_privacy_rules',
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('woodchuck_profiles.id'), primary_key=True),
        sa.Column('age_band', sa.String(12), nullable=False),
        sa.Column('declared_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('public_from', sa.DateTime(timezone=True)),
        sa.Column('private_plunge_best', sa.Integer(), server_default='0', nullable=False),
        sa.CheckConstraint("age_band IN ('under13','13to17','adult','unknown')", name='ck_account_age_band'))

def downgrade():
    # Do not automate removal of privacy evidence during a production rollback.
    raise RuntimeError('Retain age rules; use the documented compatible code recovery.')
