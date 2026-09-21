"""Standalone consent, parent access and director permission; no cohort/grants.

Retains a13screen001/002 history. No age inference or data rewrite.
"""
from alembic import op
import sqlalchemy as sa
revision='a13screen003'
down_revision='a13screen002'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('child_pending_consents',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('profile_id',sa.Integer(),sa.ForeignKey('woodchuck_profiles.id')),
        sa.Column('parent_email',sa.String(254),nullable=False),
        sa.Column('director_email',sa.String(254),nullable=False),
        sa.Column('director_name',sa.String(80),nullable=False),
        sa.Column('review_allowed',sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column('approve_hash',sa.String(64),unique=True,nullable=False),
        sa.Column('activation_hash',sa.String(64),unique=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False,index=True),
        sa.Column('approved_at',sa.DateTime(timezone=True)),
        sa.Column('confirmation_due',sa.DateTime(timezone=True)),
        sa.Column('confirmed_at',sa.DateTime(timezone=True)),
        sa.Column('notice_version',sa.String(80),nullable=False))
    op.create_table('child_consent_evidence',
        sa.Column('id',sa.Integer(),primary_key=True),
        sa.Column('profile_id',sa.Integer(),sa.ForeignKey('woodchuck_profiles.id'),nullable=False,index=True),
        sa.Column('parent_email',sa.String(254)),
        sa.Column('activation_hash',sa.String(64),unique=True),
        sa.Column('withdrawal_hash',sa.String(64),unique=True),
        sa.Column('notice_version',sa.String(80),nullable=False),
        sa.Column('notice_sha256',sa.String(64),nullable=False),
        sa.Column('approved_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('confirmed_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('withdrawn_at',sa.DateTime(timezone=True)),
        sa.Column('delete_after',sa.DateTime(timezone=True),index=True))
    with op.batch_alter_table('account_privacy_rules') as batch:
        batch.add_column(sa.Column('consent_id',sa.Integer()))
        batch.create_foreign_key('fk_age_child_consent','child_consent_evidence',['consent_id'],['id'])
    op.create_table('child_parent_access',
        sa.Column('consent_id',sa.Integer(),sa.ForeignKey('child_consent_evidence.id',ondelete='CASCADE'),primary_key=True),
        sa.Column('link_hash',sa.String(64),unique=True),sa.Column('link_expires_at',sa.DateTime(timezone=True)),
        sa.Column('requested_at',sa.DateTime(timezone=True)),sa.Column('session_hash',sa.String(64),unique=True),
        sa.Column('session_expires_at',sa.DateTime(timezone=True)),sa.Column('verifier_id',sa.Integer(),sa.ForeignKey('trusted_verifiers.id')))
    op.create_table('child_parent_age_declarations',
        sa.Column('profile_id',sa.Integer(),sa.ForeignKey('woodchuck_profiles.id'),primary_key=True),
        sa.Column('consent_id',sa.Integer(),sa.ForeignKey('child_consent_evidence.id',ondelete='SET NULL')),
        sa.Column('confirmed_at',sa.DateTime(timezone=True),nullable=False),sa.Column('wording_version',sa.String(80),nullable=False))
    op.create_table('child_director_permissions',
        sa.Column('id',sa.Integer(),primary_key=True),sa.Column('profile_id',sa.Integer(),sa.ForeignKey('woodchuck_profiles.id'),nullable=False),
        sa.Column('consent_id',sa.Integer(),sa.ForeignKey('child_consent_evidence.id'),nullable=False),
        sa.Column('email',sa.String(254),nullable=False),sa.Column('director_name',sa.String(80),nullable=False),
        sa.Column('review_allowed',sa.Boolean(),nullable=False,server_default=sa.false()),
        sa.Column('connection_id',sa.Integer(),sa.ForeignKey('student_verifier_connections.id')),
        sa.Column('authorized_at',sa.DateTime(timezone=True),nullable=False),sa.Column('revoked_at',sa.DateTime(timezone=True)),
        sa.Column('link_hash',sa.String(64),unique=True),sa.Column('link_expires_at',sa.DateTime(timezone=True)),
        sa.Column('requested_at',sa.DateTime(timezone=True)),sa.Column('session_hash',sa.String(64),unique=True),sa.Column('session_expires_at',sa.DateTime(timezone=True)))

def downgrade():
    raise RuntimeError('Retain authorization and age evidence; recover with compatible code.')
