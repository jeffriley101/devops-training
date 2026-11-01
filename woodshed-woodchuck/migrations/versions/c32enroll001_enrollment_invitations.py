"""Purpose-separated enrollment invitations; no backfill or changed old objects."""
from alembic import op
import sqlalchemy as sa

from app.operator_classroom_enrollment_contract import INVITATION_CHECKS, AUDIT_CHECKS

revision = 'c32enroll001'
down_revision = 'p31pta001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('classroom_enrollment_invitations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('purpose', sa.String(32), nullable=False),
        sa.Column('program_id', sa.Integer(), nullable=False),
        sa.Column('class_id', sa.Integer(), nullable=False),
        sa.Column('assignment_id', sa.Integer(), nullable=False),
        sa.Column('profile_id', sa.Integer(), nullable=False),
        sa.Column('issued_by_verifier_id', sa.Integer(), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('state', sa.String(10), nullable=False),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('accepted_period_id', sa.Integer(), nullable=True),
        *(sa.CheckConstraint(value, name=name) for name, value in INVITATION_CHECKS.items()),
        sa.ForeignKeyConstraint(['program_id'], ['classroom_programs.organization_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['class_id', 'program_id'], ['classroom_classes.id', 'classroom_classes.program_id'], ondelete='RESTRICT', name='fk_classroom_enrollment_class'),
        sa.ForeignKeyConstraint(['assignment_id', 'class_id', 'program_id'], ['classroom_teaching_assignments.id', 'classroom_teaching_assignments.class_id', 'classroom_teaching_assignments.program_id'], ondelete='RESTRICT', name='fk_classroom_enrollment_assignment'),
        sa.ForeignKeyConstraint(['profile_id'], ['woodchuck_profiles.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['issued_by_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['accepted_period_id'], ['classroom_membership_periods.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('id', 'program_id', 'class_id', 'profile_id', name='uq_classroom_enrollment_invitation_scope'))
    op.create_index('uq_classroom_enrollment_invitation_open', 'classroom_enrollment_invitations',
        ['assignment_id', 'profile_id'], unique=True, sqlite_where=sa.text('resolved_at IS NULL'), postgresql_where=sa.text('resolved_at IS NULL'))
    op.create_index('ix_classroom_enrollment_student_state', 'classroom_enrollment_invitations', ['profile_id', 'state'])
    op.create_table('classroom_enrollment_audit_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('invitation_id', sa.Integer(), nullable=False),
        sa.Column('program_id', sa.Integer(), nullable=False),
        sa.Column('class_id', sa.Integer(), nullable=False),
        sa.Column('profile_id', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(10), nullable=False),
        sa.Column('actor_profile_id', sa.Integer(), nullable=True),
        sa.Column('actor_verifier_id', sa.Integer(), nullable=True),
        sa.Column('membership_period_id', sa.Integer(), nullable=True),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        *(sa.CheckConstraint(value, name=name) for name, value in AUDIT_CHECKS.items()),
        sa.ForeignKeyConstraint(['invitation_id', 'program_id', 'class_id', 'profile_id'], ['classroom_enrollment_invitations.id', 'classroom_enrollment_invitations.program_id', 'classroom_enrollment_invitations.class_id', 'classroom_enrollment_invitations.profile_id'], ondelete='RESTRICT', name='fk_classroom_enrollment_audit_scope'),
        sa.ForeignKeyConstraint(['actor_profile_id'], ['woodchuck_profiles.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['actor_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['membership_period_id'], ['classroom_membership_periods.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('invitation_id', 'action', name='uq_classroom_enrollment_audit_action'))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        if bind.get_isolation_level() != 'READ COMMITTED':
            raise RuntimeError('classroom_enrollment_downgrade_requires_read_committed')
        bind.execute(sa.text('LOCK TABLE classroom_enrollment_invitations, classroom_enrollment_audit_events IN ACCESS EXCLUSIVE MODE'))
    elif bind.dialect.name == 'sqlite':
        bind.execute(sa.text('UPDATE classroom_enrollment_invitations SET id=id WHERE 0'))
    else:
        raise RuntimeError('classroom_enrollment_unsupported_database')
    for name in ['classroom_enrollment_invitations', 'classroom_enrollment_audit_events']:
        if bind.scalar(sa.text('SELECT count(*) FROM ' + name)):
            raise RuntimeError('classroom_enrollment_evidence_in_use: downgrade refused')
    op.drop_table('classroom_enrollment_audit_events')
    op.drop_index('ix_classroom_enrollment_student_state', table_name='classroom_enrollment_invitations')
    op.drop_index('uq_classroom_enrollment_invitation_open', table_name='classroom_enrollment_invitations')
    op.drop_table('classroom_enrollment_invitations')
