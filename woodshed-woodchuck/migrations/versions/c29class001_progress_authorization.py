"""Explicit student reporting choices. No backfill or existing access changes."""
from alembic import op
import sqlalchemy as sa

revision = "c29class001"
down_revision = "c28class001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('classroom_progress_authorizations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('program_id', sa.Integer(), nullable=False),
        sa.Column('class_id', sa.Integer(), nullable=False),
        sa.Column('profile_id', sa.Integer(), nullable=False),
        sa.Column('membership_id', sa.Integer(), nullable=False),
        sa.Column('membership_period_id', sa.Integer(), nullable=False),
        sa.Column('entitlement_id', sa.Integer(), nullable=False),
        sa.Column('account_declared_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('class_activation_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source_audit_id', sa.Integer(), nullable=True),
        sa.Column('scope_version', sa.String(length=80), nullable=False),
        sa.Column('notice_version', sa.String(length=80), nullable=False),
        sa.Column('notice_digest', sa.String(length=64), nullable=False),
        sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('accepted_by_profile_id', sa.Integer(), nullable=False),
        sa.Column('acceptance_session_version', sa.Integer(), nullable=False),
        sa.Column('withdrawn_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('withdrawn_by_profile_id', sa.Integer(), nullable=True),
        sa.Column('withdrawal_reason', sa.String(length=12), nullable=True),
        sa.CheckConstraint("(withdrawn_at IS NULL AND withdrawn_by_profile_id IS NULL AND withdrawal_reason IS NULL) OR (withdrawn_at IS NOT NULL AND withdrawn_at >= accepted_at AND withdrawn_by_profile_id IS NOT NULL AND withdrawn_by_profile_id = profile_id AND withdrawal_reason IS NOT NULL AND withdrawal_reason IN ('withdrawn', 'replaced'))", name='ck_classroom_progress_withdrawal'),
        sa.CheckConstraint('acceptance_session_version >= 0', name='ck_classroom_progress_session'),
        sa.CheckConstraint('accepted_by_profile_id = profile_id', name='ck_classroom_progress_acceptor'),
        sa.CheckConstraint('length(notice_digest) = 64', name='ck_classroom_progress_notice_digest'),
        sa.CheckConstraint('length(trim(notice_version)) BETWEEN 1 AND 80', name='ck_classroom_progress_notice'),
        sa.CheckConstraint('length(trim(scope_version)) BETWEEN 1 AND 80', name='ck_classroom_progress_scope'),
        sa.ForeignKeyConstraint(['accepted_by_profile_id'], ['woodchuck_profiles.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['class_id', 'program_id'], ['classroom_classes.id', 'classroom_classes.program_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['entitlement_id', 'program_id'], ['classroom_entitlements.id', 'classroom_entitlements.program_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['membership_id'], ['classroom_student_memberships.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['membership_period_id'], ['classroom_membership_periods.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['profile_id'], ['woodchuck_profiles.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['source_audit_id'], ['classroom_s2_audit_events.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['withdrawn_by_profile_id'], ['woodchuck_profiles.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'class_id', 'program_id', name='uq_classroom_progress_scope')
    )
    op.create_index('uq_classroom_progress_open', 'classroom_progress_authorizations', ['profile_id', 'class_id'], unique=True, sqlite_where=sa.text('withdrawn_at IS NULL'), postgresql_where=sa.text('withdrawn_at IS NULL'))
    op.create_table('classroom_progress_recipients',
        sa.Column('authorization_id', sa.Integer(), nullable=False),
        sa.Column('assignment_id', sa.Integer(), nullable=False),
        sa.Column('class_id', sa.Integer(), nullable=False),
        sa.Column('program_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['assignment_id', 'class_id', 'program_id'], ['classroom_teaching_assignments.id', 'classroom_teaching_assignments.class_id', 'classroom_teaching_assignments.program_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['authorization_id', 'class_id', 'program_id'], ['classroom_progress_authorizations.id', 'classroom_progress_authorizations.class_id', 'classroom_progress_authorizations.program_id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('authorization_id', 'assignment_id')
    )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("LOCK TABLE classroom_progress_authorizations, classroom_progress_recipients IN ACCESS EXCLUSIVE MODE"))
    elif bind.dialect.name == "sqlite":
        bind.execute(sa.text("UPDATE classroom_progress_authorizations SET id = id WHERE 0"))
    else:
        raise RuntimeError("classroom_progress_unsupported_database")
    if bind.scalar(sa.text("SELECT count(*) FROM classroom_progress_authorizations")):
        raise RuntimeError("classroom_progress_evidence_in_use: downgrade refused")
    op.drop_table("classroom_progress_recipients")
    op.drop_index("uq_classroom_progress_open", table_name="classroom_progress_authorizations")
    op.drop_table("classroom_progress_authorizations")
