"""Add inactive Classroom S2 entitlement, entry authority and lifecycle evidence.

No S1 relationships or consumer/Team/parent/account authority are backfilled.
Every new table must be unused before downgrade; existing period history also
prevents removing its new overlap guard.
"""
from alembic import op
import sqlalchemy as sa

revision = "c23class001"
down_revision = "c22class001"
branch_labels = None
depends_on = None

S2_TABLES = (
    "classroom_entitlements", "classroom_class_states", "classroom_entry_codes",
    "classroom_membership_holds", "classroom_s2_audit_events",
)

SQLITE_PERIOD_INSERT = """CREATE TRIGGER classroom_period_no_overlap_insert
BEFORE INSERT ON classroom_membership_periods
FOR EACH ROW WHEN EXISTS (
    SELECT 1 FROM classroom_membership_periods p
    WHERE p.membership_id = NEW.membership_id
      AND (NEW.ended_at IS NULL OR p.starts_at < NEW.ended_at)
      AND (p.ended_at IS NULL OR NEW.starts_at < p.ended_at)
)
BEGIN SELECT RAISE(ABORT, 'classroom_membership_period_overlap'); END"""

SQLITE_PERIOD_UPDATE = """CREATE TRIGGER classroom_period_no_overlap_update
BEFORE UPDATE ON classroom_membership_periods
FOR EACH ROW WHEN EXISTS (
    SELECT 1 FROM classroom_membership_periods p
    WHERE p.membership_id = NEW.membership_id AND p.id <> NEW.id
      AND (NEW.ended_at IS NULL OR p.starts_at < NEW.ended_at)
      AND (p.ended_at IS NULL OR NEW.starts_at < p.ended_at)
)
BEGIN SELECT RAISE(ABORT, 'classroom_membership_period_overlap'); END"""

POSTGRES_PERIOD_FUNCTION = """CREATE FUNCTION classroom_period_no_overlap() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    PERFORM p.organization_id FROM classroom_programs p
    JOIN classroom_classes c ON c.program_id = p.organization_id
    JOIN classroom_student_memberships m ON m.class_id = c.id
    WHERE m.id = NEW.membership_id FOR UPDATE OF p;
    PERFORM c.id FROM classroom_classes c
    JOIN classroom_student_memberships m ON m.class_id = c.id
    WHERE m.id = NEW.membership_id FOR UPDATE OF c;
    PERFORM id FROM classroom_student_memberships
    WHERE id = NEW.membership_id FOR UPDATE;
    IF EXISTS (
        SELECT 1 FROM classroom_membership_periods p
        WHERE p.membership_id = NEW.membership_id AND p.id <> NEW.id
          AND (NEW.ended_at IS NULL OR p.starts_at < NEW.ended_at)
          AND (p.ended_at IS NULL OR NEW.starts_at < p.ended_at)
    ) THEN
        RAISE EXCEPTION 'classroom_membership_period_overlap' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$"""

POSTGRES_PERIOD_TRIGGER = """CREATE TRIGGER classroom_period_no_overlap
BEFORE INSERT OR UPDATE ON classroom_membership_periods
FOR EACH ROW EXECUTE FUNCTION classroom_period_no_overlap()"""


def _fence_period_writers():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("LOCK TABLE classroom_programs, classroom_classes, "
                             "classroom_student_memberships, classroom_membership_periods "
                             "IN ACCESS EXCLUSIVE MODE"))
    elif bind.dialect.name == "sqlite":
        bind.execute(sa.text("UPDATE classroom_programs SET owner_version = owner_version WHERE 0"))


def _validate_existing_periods():
    _fence_period_writers()
    if op.get_bind().scalar(sa.text("""SELECT count(*) FROM classroom_membership_periods a
        JOIN classroom_membership_periods b ON a.membership_id = b.membership_id AND a.id < b.id
        WHERE (a.ended_at IS NULL OR b.starts_at < a.ended_at)
          AND (b.ended_at IS NULL OR a.starts_at < b.ended_at)""")):
        raise RuntimeError("classroom_existing_period_overlap: migration refused")


def _install_period_guards():
    if op.get_bind().dialect.name == "postgresql":
        op.execute(POSTGRES_PERIOD_FUNCTION)
        op.execute(POSTGRES_PERIOD_TRIGGER)
    elif op.get_bind().dialect.name == "sqlite":
        op.execute(SQLITE_PERIOD_INSERT)
        op.execute(SQLITE_PERIOD_UPDATE)
    else:
        raise RuntimeError("classroom_unsupported_database")


def upgrade():
    _validate_existing_periods()
    op.create_table('classroom_entitlements',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('program_id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=20), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('class_limit', sa.Integer(), nullable=False),
    sa.Column('teacher_limit', sa.Integer(), nullable=False),
    sa.Column('approved_by_verifier_id', sa.Integer(), nullable=True),
    sa.Column('approved_by_admin', sa.String(length=64), nullable=True),
    sa.Column('provenance', sa.String(length=100), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("(source = 'trial' AND approved_by_verifier_id IS NOT NULL AND approved_by_admin IS NULL) OR (source = 'institutional' AND approved_by_verifier_id IS NULL AND approved_by_admin IS NOT NULL)", name='ck_classroom_entitlement_approval'),
    sa.CheckConstraint("(status = 'active' AND ended_at IS NULL) OR (status = 'ended' AND ended_at IS NOT NULL)", name='ck_classroom_entitlement_end'),
    sa.CheckConstraint("source IN ('trial', 'institutional')", name='ck_classroom_entitlement_source'),
    sa.CheckConstraint("status IN ('active', 'ended')", name='ck_classroom_entitlement_status'),
    sa.CheckConstraint('class_limit >= 0 AND teacher_limit >= 0', name='ck_classroom_entitlement_limits'),
    sa.CheckConstraint('ends_at > starts_at', name='ck_classroom_entitlement_dates'),
    sa.CheckConstraint('length(trim(provenance)) BETWEEN 1 AND 100', name='ck_classroom_entitlement_provenance'),
    sa.ForeignKeyConstraint(['approved_by_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['program_id'], ['classroom_programs.organization_id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'program_id', name='uq_classroom_entitlement_scope')
    )
    op.create_index('ix_classroom_entitlement_program', 'classroom_entitlements', ['program_id'], unique=False)
    op.create_index('uq_classroom_entitlement_trial', 'classroom_entitlements', ['program_id'], unique=True, sqlite_where=sa.text("source = 'trial'"), postgresql_where=sa.text("source = 'trial'"))
    op.create_table('classroom_class_states',
    sa.Column('class_id', sa.Integer(), nullable=False),
    sa.Column('program_id', sa.Integer(), nullable=False),
    sa.Column('state', sa.String(length=10), nullable=False),
    sa.Column('enrollment_open', sa.Boolean(), nullable=False),
    sa.Column('changed_by_verifier_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("state <> 'archived' OR enrollment_open = false", name='ck_classroom_archived_closed'),
    sa.CheckConstraint("state IN ('active', 'archived')", name='ck_classroom_class_state'),
    sa.ForeignKeyConstraint(['changed_by_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['class_id', 'program_id'], ['classroom_classes.id', 'classroom_classes.program_id'], name='fk_classroom_state_scope', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('class_id')
    )
    op.create_index('ix_classroom_state_program', 'classroom_class_states', ['program_id'], unique=False)
    op.create_table('classroom_entry_codes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('program_id', sa.Integer(), nullable=False),
    sa.Column('class_id', sa.Integer(), nullable=False),
    sa.Column('generation', sa.Integer(), nullable=False),
    sa.Column('digest', sa.String(length=64), nullable=False),
    sa.Column('is_current', sa.Boolean(), nullable=False),
    sa.Column('issued_by_verifier_id', sa.Integer(), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_by_verifier_id', sa.Integer(), nullable=True),
    sa.CheckConstraint('(is_current = true AND revoked_at IS NULL AND revoked_by_verifier_id IS NULL) OR (is_current = false AND revoked_at IS NOT NULL AND revoked_by_verifier_id IS NOT NULL)', name='ck_classroom_code_revocation'),
    sa.CheckConstraint('generation >= 1', name='ck_classroom_code_generation'),
    sa.CheckConstraint('length(digest) = 64', name='ck_classroom_code_digest'),
    sa.ForeignKeyConstraint(['class_id', 'program_id'], ['classroom_classes.id', 'classroom_classes.program_id'], name='fk_classroom_code_scope', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['issued_by_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['revoked_by_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('class_id', 'generation', name='uq_classroom_code_generation'),
    sa.UniqueConstraint('id', 'class_id', 'program_id', name='uq_classroom_code_scope')
    )
    op.create_index('uq_classroom_code_current_class', 'classroom_entry_codes', ['class_id'], unique=True, sqlite_where=sa.text('is_current = true'), postgresql_where=sa.text('is_current = true'))
    op.create_index('uq_classroom_code_current_digest', 'classroom_entry_codes', ['program_id', 'digest'], unique=True, sqlite_where=sa.text('is_current = true'), postgresql_where=sa.text('is_current = true'))
    op.create_table('classroom_membership_holds',
    sa.Column('membership_id', sa.Integer(), nullable=False),
    sa.Column('state', sa.String(length=12), nullable=False),
    sa.Column('reason', sa.String(length=20), nullable=False),
    sa.Column('held_by_verifier_id', sa.Integer(), nullable=False),
    sa.Column('held_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('released_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('released_by_verifier_id', sa.Integer(), nullable=True),
    sa.CheckConstraint("reason IN ('conduct', 'admin_removal')", name='ck_classroom_hold_reason'),
    sa.CheckConstraint("state IN ('suspended', 'removed')", name='ck_classroom_hold_state'),
    sa.CheckConstraint('(released_at IS NULL AND released_by_verifier_id IS NULL) OR (released_at IS NOT NULL AND released_by_verifier_id IS NOT NULL AND released_at >= held_at)', name='ck_classroom_hold_release'),
    sa.ForeignKeyConstraint(['held_by_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['membership_id'], ['classroom_student_memberships.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['released_by_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('membership_id')
    )
    op.create_table('classroom_s2_audit_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('program_id', sa.Integer(), nullable=False),
    sa.Column('class_id', sa.Integer(), nullable=True),
    sa.Column('profile_id', sa.Integer(), nullable=True),
    sa.Column('membership_id', sa.Integer(), nullable=True),
    sa.Column('actor_verifier_id', sa.Integer(), nullable=True),
    sa.Column('actor_profile_id', sa.Integer(), nullable=True),
    sa.Column('actor_admin', sa.String(length=64), nullable=True),
    sa.Column('action', sa.String(length=30), nullable=False),
    sa.Column('reason', sa.String(length=40), nullable=True),
    sa.Column('entitlement_id', sa.Integer(), nullable=True),
    sa.Column('code_id', sa.Integer(), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("action IN ('trial_started', 'institutional_created', 'institutional_changed', 'institutional_ended', 'class_activated', 'class_archived', 'class_reactivated', 'enrollment_opened', 'enrollment_closed', 'code_issued', 'code_rotated', 'code_revoked', 'student_joined', 'student_left', 'student_suspended', 'student_removed', 'student_reinstated', 'teacher_activated', 'teacher_deactivated')", name='ck_classroom_s2_audit_action'),
    sa.CheckConstraint('(CASE WHEN actor_verifier_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN actor_profile_id IS NOT NULL THEN 1 ELSE 0 END + CASE WHEN actor_admin IS NOT NULL THEN 1 ELSE 0 END) = 1', name='ck_classroom_s2_audit_actor'),
    sa.ForeignKeyConstraint(['actor_profile_id'], ['woodchuck_profiles.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['actor_verifier_id'], ['trusted_verifiers.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['class_id', 'program_id'], ['classroom_classes.id', 'classroom_classes.program_id'], name='fk_classroom_s2_audit_class', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['code_id', 'class_id', 'program_id'], ['classroom_entry_codes.id', 'classroom_entry_codes.class_id', 'classroom_entry_codes.program_id'], name='fk_classroom_s2_audit_code', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['entitlement_id', 'program_id'], ['classroom_entitlements.id', 'classroom_entitlements.program_id'], name='fk_classroom_s2_audit_entitlement', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['membership_id'], ['classroom_student_memberships.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['profile_id'], ['woodchuck_profiles.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['program_id'], ['classroom_programs.organization_id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_classroom_s2_audit_program_time', 'classroom_s2_audit_events', ['program_id', 'occurred_at'], unique=False)
    _install_period_guards()


def downgrade():
    _fence_period_writers()
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("LOCK TABLE " + ", ".join(S2_TABLES) + " IN ACCESS EXCLUSIVE MODE"))
    for table in (*S2_TABLES, "classroom_membership_periods"):
        if bind.scalar(sa.text(f"SELECT count(*) FROM {table}")):
            raise RuntimeError("classroom_s2_authority_or_history_in_use: downgrade refused")
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER classroom_period_no_overlap ON classroom_membership_periods")
        op.execute("DROP FUNCTION classroom_period_no_overlap()")
    elif bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER classroom_period_no_overlap_insert")
        op.execute("DROP TRIGGER classroom_period_no_overlap_update")
    for table in reversed(S2_TABLES):
        op.drop_table(table)
