"""Add Program-private roster identity; no backfill or prior object changes."""
from alembic import op
import sqlalchemy as sa

from app.operator_classroom_identity_contract import IDENTITY_CHECKS, AUDIT_CHECKS

revision = 'c33identity001'
down_revision = 'c32enroll001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('classroom_roster_identities',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('program_id', sa.Integer(), nullable=False),
        sa.Column('profile_id', sa.Integer(), nullable=False),
        sa.Column('school_name', sa.String(150), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        *(sa.CheckConstraint(value, name=name) for name, value in IDENTITY_CHECKS.items()),
        sa.ForeignKeyConstraint(['program_id'], ['classroom_programs.organization_id'], ondelete='RESTRICT'),
        sa.ForeignKeyConstraint(['profile_id'], ['woodchuck_profiles.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('program_id', 'profile_id', name='uq_classroom_identity_program_profile'),
        sa.UniqueConstraint('id', 'program_id', 'profile_id', name='uq_classroom_identity_scope'))
    op.create_table('classroom_roster_identity_audits',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('identity_id', sa.Integer(), nullable=False),
        sa.Column('program_id', sa.Integer(), nullable=False),
        sa.Column('profile_id', sa.Integer(), nullable=False),
        sa.Column('class_id', sa.Integer(), nullable=False),
        sa.Column('actor_verifier_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('action', sa.String(10), nullable=False),
        sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
        *(sa.CheckConstraint(value, name=name) for name, value in AUDIT_CHECKS.items()),
        sa.ForeignKeyConstraint(['identity_id', 'program_id', 'profile_id'],
            ['classroom_roster_identities.id', 'classroom_roster_identities.program_id', 'classroom_roster_identities.profile_id'],
            ondelete='RESTRICT', name='fk_classroom_identity_audit_scope'),
        sa.ForeignKeyConstraint(['class_id', 'program_id'], ['classroom_classes.id', 'classroom_classes.program_id'],
            ondelete='RESTRICT', name='fk_classroom_identity_audit_class'),
        sa.ForeignKeyConstraint(['actor_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('identity_id', 'version', name='uq_classroom_identity_audit_version'))


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        if bind.get_isolation_level() != 'READ COMMITTED':
            raise RuntimeError('classroom_identity_downgrade_requires_read_committed')
        bind.execute(sa.text('LOCK TABLE classroom_roster_identities, classroom_roster_identity_audits IN ACCESS EXCLUSIVE MODE'))
    elif bind.dialect.name == 'sqlite':
        bind.execute(sa.text('UPDATE classroom_roster_identities SET id=id WHERE 0'))
    else:
        raise RuntimeError('classroom_identity_unsupported_database')
    for name in ['classroom_roster_identities', 'classroom_roster_identity_audits']:
        if bind.scalar(sa.text('SELECT count(*) FROM ' + name)):
            raise RuntimeError('classroom_identity_evidence_in_use: downgrade refused')
    op.drop_table('classroom_roster_identity_audits')
    op.drop_table('classroom_roster_identities')
