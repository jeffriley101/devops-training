"""Install disabled, empty Classroom S1 relationship structures.

No existing Organization, verifier, student, Team, or entitlement is promoted.
Downgrade is permitted only while every new table remains empty.
"""
from alembic import op
import sqlalchemy as sa

revision = "c22class001"
down_revision = "p21team001"
branch_labels = None
depends_on = None

CLASSROOM_TABLES = (
    "classroom_programs", "classroom_role_grants", "classroom_classes",
    "classroom_teaching_assignments", "classroom_student_memberships",
    "classroom_membership_periods", "classroom_ownership_transfers",
    "classroom_audit_events",
)


def upgrade():
    op.create_table('classroom_programs',
    sa.Column('organization_id', sa.Integer(), nullable=False),
    sa.Column('owner_verifier_id', sa.Integer(), nullable=False),
    sa.Column('owner_version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('owner_version >= 1', name='ck_classroom_program_owner_version'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['owner_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('organization_id')
    )
    op.create_index(op.f('ix_classroom_programs_owner_verifier_id'), 'classroom_programs', ['owner_verifier_id'], unique=False)
    op.create_table('classroom_classes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('program_id', sa.Integer(), nullable=False),
    sa.Column('display_name', sa.String(length=150), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint('length(trim(display_name)) BETWEEN 1 AND 150', name='ck_classroom_class_name'),
    sa.ForeignKeyConstraint(['program_id'], ['classroom_programs.organization_id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'program_id', name='uq_classroom_class_id_program')
    )
    op.create_index(op.f('ix_classroom_classes_program_id'), 'classroom_classes', ['program_id'], unique=False)
    op.create_table('classroom_ownership_transfers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('program_id', sa.Integer(), nullable=False),
    sa.Column('initiator_verifier_id', sa.Integer(), nullable=False),
    sa.Column('recipient_verifier_id', sa.Integer(), nullable=False),
    sa.Column('owner_version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=12), server_default='pending', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(status = 'pending' AND resolved_at IS NULL) OR (status <> 'pending' AND resolved_at IS NOT NULL AND resolved_at >= created_at)", name='ck_classroom_transfer_resolution'),
    sa.CheckConstraint("status IN ('pending', 'accepted', 'cancelled', 'superseded')", name='ck_classroom_transfer_status'),
    sa.CheckConstraint('initiator_verifier_id <> recipient_verifier_id', name='ck_classroom_transfer_distinct_adults'),
    sa.CheckConstraint('owner_version >= 1', name='ck_classroom_transfer_owner_version'),
    sa.ForeignKeyConstraint(['initiator_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['program_id'], ['classroom_programs.organization_id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['recipient_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'program_id', name='uq_classroom_transfer_id_program')
    )
    op.create_index('uq_classroom_transfer_pending', 'classroom_ownership_transfers', ['program_id'], unique=True, sqlite_where=sa.text("status = 'pending'"), postgresql_where=sa.text("status = 'pending'"))
    op.create_table('classroom_role_grants',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('program_id', sa.Integer(), nullable=False),
    sa.Column('verifier_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_reason', sa.String(length=10), nullable=True),
    sa.Column('granted_by_verifier_id', sa.Integer(), nullable=False),
    sa.Column('ended_by_verifier_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("(ended_at IS NULL AND ended_by_verifier_id IS NULL AND ended_reason IS NULL) OR (ended_at IS NOT NULL AND ended_by_verifier_id IS NOT NULL AND ended_reason IS NOT NULL AND ended_reason IN ('revoked', 'departed'))", name='ck_classroom_role_end_evidence'),
    sa.CheckConstraint("role IN ('head_director', 'admin', 'code_manager', 'billing')", name='ck_classroom_role_kind'),
    sa.CheckConstraint('ended_at IS NULL OR ended_at > starts_at', name='ck_classroom_role_period'),
    sa.ForeignKeyConstraint(['ended_by_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['granted_by_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['program_id'], ['classroom_programs.organization_id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'program_id', name='uq_classroom_role_id_program')
    )
    op.create_index(op.f('ix_classroom_role_grants_verifier_id'), 'classroom_role_grants', ['verifier_id'], unique=False)
    op.create_index('uq_classroom_role_open', 'classroom_role_grants', ['program_id', 'verifier_id', 'role'], unique=True, sqlite_where=sa.text('ended_at IS NULL'), postgresql_where=sa.text('ended_at IS NULL'))
    op.create_table('classroom_student_memberships',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('class_id', sa.Integer(), nullable=False),
    sa.Column('profile_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(['class_id'], ['classroom_classes.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['profile_id'], ['woodchuck_profiles.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('class_id', 'profile_id', name='uq_classroom_membership_class_student')
    )
    op.create_index(op.f('ix_classroom_student_memberships_profile_id'), 'classroom_student_memberships', ['profile_id'], unique=False)
    op.create_table('classroom_teaching_assignments',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('program_id', sa.Integer(), nullable=False),
    sa.Column('class_id', sa.Integer(), nullable=False),
    sa.Column('verifier_id', sa.Integer(), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_reason', sa.String(length=10), nullable=True),
    sa.Column('granted_by_verifier_id', sa.Integer(), nullable=False),
    sa.Column('ended_by_verifier_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("(ended_at IS NULL AND ended_by_verifier_id IS NULL AND ended_reason IS NULL) OR (ended_at IS NOT NULL AND ended_by_verifier_id IS NOT NULL AND ended_reason IS NOT NULL AND ended_reason IN ('revoked', 'departed'))", name='ck_classroom_assignment_end_evidence'),
    sa.CheckConstraint('ended_at IS NULL OR ended_at > starts_at', name='ck_classroom_assignment_period'),
    sa.ForeignKeyConstraint(['class_id', 'program_id'], ['classroom_classes.id', 'classroom_classes.program_id'], name='fk_classroom_assignment_class_program', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['ended_by_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['granted_by_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'class_id', 'program_id', name='uq_classroom_assignment_scope')
    )
    op.create_index('ix_classroom_assignment_program_verifier', 'classroom_teaching_assignments', ['program_id', 'verifier_id'], unique=False)
    op.create_index('uq_classroom_assignment_open', 'classroom_teaching_assignments', ['class_id', 'verifier_id'], unique=True, sqlite_where=sa.text('ended_at IS NULL'), postgresql_where=sa.text('ended_at IS NULL'))
    op.create_table('classroom_audit_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('program_id', sa.Integer(), nullable=False),
    sa.Column('class_id', sa.Integer(), nullable=True),
    sa.Column('actor_verifier_id', sa.Integer(), nullable=False),
    sa.Column('target_verifier_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=30), nullable=False),
    sa.Column('role_grant_id', sa.Integer(), nullable=True),
    sa.Column('assignment_id', sa.Integer(), nullable=True),
    sa.Column('transfer_id', sa.Integer(), nullable=True),
    sa.Column('old_owner_id', sa.Integer(), nullable=True),
    sa.Column('new_owner_id', sa.Integer(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("action <> 'class_created' OR class_id IS NOT NULL", name='ck_classroom_audit_class_evidence'),
    sa.CheckConstraint("action <> 'ownership_transferred' OR (old_owner_id IS NOT NULL AND new_owner_id IS NOT NULL AND old_owner_id <> new_owner_id)", name='ck_classroom_audit_owner_evidence'),
    sa.CheckConstraint("action <> 'program_provisioned' OR (new_owner_id IS NOT NULL AND target_verifier_id IS NOT NULL)", name='ck_classroom_audit_provision_evidence'),
    sa.CheckConstraint("action IN ('program_provisioned', 'role_granted', 'role_revoked', 'class_created', 'teacher_assigned', 'teacher_revoked', 'teacher_departed', 'transfer_proposed', 'transfer_cancelled', 'transfer_superseded', 'ownership_transferred')", name='ck_classroom_audit_action'),
    sa.CheckConstraint("action NOT IN ('role_granted', 'role_revoked') OR (role_grant_id IS NOT NULL AND target_verifier_id IS NOT NULL)", name='ck_classroom_audit_role_evidence'),
    sa.CheckConstraint("action NOT IN ('teacher_assigned', 'teacher_revoked', 'teacher_departed') OR (assignment_id IS NOT NULL AND class_id IS NOT NULL AND target_verifier_id IS NOT NULL)", name='ck_classroom_audit_teacher_evidence'),
    sa.CheckConstraint("action NOT IN ('transfer_proposed', 'transfer_cancelled', 'transfer_superseded', 'ownership_transferred') OR (transfer_id IS NOT NULL AND target_verifier_id IS NOT NULL)", name='ck_classroom_audit_transfer_evidence'),
    sa.ForeignKeyConstraint(['actor_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['assignment_id', 'class_id', 'program_id'], ['classroom_teaching_assignments.id', 'classroom_teaching_assignments.class_id', 'classroom_teaching_assignments.program_id'], name='fk_classroom_audit_assignment_scope', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['class_id', 'program_id'], ['classroom_classes.id', 'classroom_classes.program_id'], name='fk_classroom_audit_class_scope', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['new_owner_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['old_owner_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['program_id'], ['classroom_programs.organization_id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['role_grant_id', 'program_id'], ['classroom_role_grants.id', 'classroom_role_grants.program_id'], name='fk_classroom_audit_role_scope', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['target_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['transfer_id', 'program_id'], ['classroom_ownership_transfers.id', 'classroom_ownership_transfers.program_id'], name='fk_classroom_audit_transfer_scope', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_classroom_audit_program_time', 'classroom_audit_events', ['program_id', 'occurred_at'], unique=False)
    op.create_table('classroom_membership_periods',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('membership_id', sa.Integer(), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('state', sa.String(length=10), nullable=False),
    sa.Column('ended_reason', sa.String(length=10), nullable=True),
    sa.CheckConstraint("(ended_at IS NULL AND ended_reason IS NULL) OR (ended_at IS NOT NULL AND ended_reason IS NOT NULL AND ended_reason IN ('departed', 'changed'))", name='ck_classroom_membership_period_end'),
    sa.CheckConstraint("state IN ('active', 'held')", name='ck_classroom_membership_period_state'),
    sa.CheckConstraint('ended_at IS NULL OR ended_at > starts_at', name='ck_classroom_membership_period_dates'),
    sa.ForeignKeyConstraint(['membership_id'], ['classroom_student_memberships.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('membership_id', 'starts_at', name='uq_classroom_membership_period_start')
    )
    op.create_index('uq_classroom_membership_period_open', 'classroom_membership_periods', ['membership_id'], unique=True, sqlite_where=sa.text('ended_at IS NULL'), postgresql_where=sa.text('ended_at IS NULL'))


def downgrade():
    bind = op.get_bind()
    # Fence writers before checking emptiness: a concurrent first Program must
    # never commit between the check and destructive DDL. Match service order
    # by acquiring the Program lock first; no legacy/PTA lock is involved.
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("LOCK TABLE " + ", ".join(CLASSROOM_TABLES) + " IN ACCESS EXCLUSIVE MODE"))
    elif bind.dialect.name == "sqlite":
        bind.execute(sa.text("UPDATE classroom_programs SET owner_version = owner_version WHERE 0"))
    for table in CLASSROOM_TABLES:
        if bind.scalar(sa.text(f"SELECT count(*) FROM {table}")):
            raise RuntimeError("classroom_authority_or_history_in_use: downgrade refused")
    op.drop_index('uq_classroom_membership_period_open', table_name='classroom_membership_periods', sqlite_where=sa.text('ended_at IS NULL'), postgresql_where=sa.text('ended_at IS NULL'))
    op.drop_table('classroom_membership_periods')
    op.drop_index('ix_classroom_audit_program_time', table_name='classroom_audit_events')
    op.drop_table('classroom_audit_events')
    op.drop_index('uq_classroom_assignment_open', table_name='classroom_teaching_assignments', sqlite_where=sa.text('ended_at IS NULL'), postgresql_where=sa.text('ended_at IS NULL'))
    op.drop_index('ix_classroom_assignment_program_verifier', table_name='classroom_teaching_assignments')
    op.drop_table('classroom_teaching_assignments')
    op.drop_index(op.f('ix_classroom_student_memberships_profile_id'), table_name='classroom_student_memberships')
    op.drop_table('classroom_student_memberships')
    op.drop_index('uq_classroom_role_open', table_name='classroom_role_grants', sqlite_where=sa.text('ended_at IS NULL'), postgresql_where=sa.text('ended_at IS NULL'))
    op.drop_index(op.f('ix_classroom_role_grants_verifier_id'), table_name='classroom_role_grants')
    op.drop_table('classroom_role_grants')
    op.drop_index('uq_classroom_transfer_pending', table_name='classroom_ownership_transfers', sqlite_where=sa.text("status = 'pending'"), postgresql_where=sa.text("status = 'pending'"))
    op.drop_table('classroom_ownership_transfers')
    op.drop_index(op.f('ix_classroom_classes_program_id'), table_name='classroom_classes')
    op.drop_table('classroom_classes')
    op.drop_index(op.f('ix_classroom_programs_owner_verifier_id'), table_name='classroom_programs')
    op.drop_table('classroom_programs')
