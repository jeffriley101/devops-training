"""Add KWS Test bindings; keep existing accounts and consent evidence intact."""
from alembic import op
import sqlalchemy as sa

revision = 'a13screen004'
down_revision = 'a13screen003'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('child_kws_verifications',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('pending_id', sa.Integer(), sa.ForeignKey('child_pending_consents.id'), unique=True, nullable=False),
        sa.Column('payload_hash', sa.String(64), unique=True, nullable=False),
        sa.Column('environment', sa.String(16), nullable=False),
        sa.Column('org_id', sa.String(128), nullable=False),
        sa.Column('product_id', sa.String(128)),
        sa.Column('binding_sha256', sa.String(64), nullable=False),
        sa.Column('notice_sha256', sa.String(64), nullable=False),
        sa.Column('profile_session_version', sa.Integer()),
        sa.Column('prior_consent_id', sa.Integer()),
        sa.Column('guardian_attested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('notice_accepted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('account_allowed', sa.Boolean(), nullable=False),
        sa.Column('director_allowed', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('state', sa.String(24), nullable=False),
        sa.Column('transaction_hash', sa.String(64), unique=True),
        sa.Column('completed_at', sa.DateTime(timezone=True)),
        sa.Column('activated_consent_id', sa.Integer(), sa.ForeignKey('child_consent_evidence.id')))
    op.create_table('child_kws_email_budgets',
        sa.Column('email_hash', sa.String(64), primary_key=True),
        sa.Column('sent_at', sa.JSON(), nullable=False))


def downgrade():
    raise RuntimeError('Retain consent/replay evidence; use compatible repair-forward code, not a downgrade.')
