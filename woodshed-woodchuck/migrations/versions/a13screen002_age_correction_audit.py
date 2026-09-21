"""Protected support correction provenance, without student data rewrites."""
from alembic import op
import sqlalchemy as sa
revision = 'a13screen002'
down_revision = 'a13screen001'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('account_age_corrections',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('profile_id', sa.Integer(), sa.ForeignKey('woodchuck_profiles.id'), nullable=False),
        sa.Column('previous_band', sa.String(12), nullable=False),
        sa.Column('corrected_band', sa.String(12), nullable=False),
        sa.Column('corrected_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('actor_fingerprint', sa.String(64), nullable=False),
        sa.Column('case_reference', sa.String(64), nullable=False),
        sa.Column('wording_version', sa.String(40), nullable=False))

def downgrade():
    raise RuntimeError('Retain age correction evidence; use compatible code recovery.')
