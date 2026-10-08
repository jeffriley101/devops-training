"""Add explicit Classroom reporting periods and minimal transactional audit.

No student benefits, reporting grants, or account permissions are backfilled.
Downgrade refuses any reporting evidence instead of erasing it.
"""
from alembic import op
import sqlalchemy as sa

revision = "c24class001"
down_revision = "c23class001"
branch_labels = None
depends_on = None

S3_TABLES = ("classroom_reporting_periods", "classroom_s3_audit_events")


def upgrade():
    op.create_table('classroom_reporting_periods',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('program_id', sa.Integer(), nullable=False),
    sa.Column('class_id', sa.Integer(), nullable=False),
    sa.Column('membership_id', sa.Integer(), nullable=False),
    sa.Column('membership_period_id', sa.Integer(), nullable=False),
    sa.Column('profile_id', sa.Integer(), nullable=False),
    sa.Column('authorizer_profile_id', sa.Integer(), nullable=False),
    sa.Column('account_declared_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('consent_id', sa.Integer(), nullable=True),
    sa.Column('entitlement_id', sa.Integer(), nullable=False),
    sa.Column('class_activation_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('source_audit_id', sa.Integer(), nullable=True),
    sa.Column('scope_version', sa.String(length=80), nullable=False),
    sa.Column('notice_version', sa.String(length=80), nullable=False),
    sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_by_profile_id', sa.Integer(), nullable=True),
    sa.Column('ended_reason', sa.String(length=30), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("(ended_at IS NULL AND ended_by_profile_id IS NULL AND ended_reason IS NULL) OR (ended_at IS NOT NULL AND ended_at >= starts_at AND ended_by_profile_id IS NOT NULL AND ended_reason IS NOT NULL AND ended_reason IN ('withdrawn', 'source_changed'))", name='ck_classroom_reporting_end'),
    sa.CheckConstraint('authorizer_profile_id = profile_id', name='ck_classroom_reporting_authorizer'),
    sa.CheckConstraint('ended_by_profile_id IS NULL OR ended_by_profile_id = profile_id', name='ck_classroom_reporting_revoker'),
    sa.CheckConstraint('length(trim(notice_version)) BETWEEN 1 AND 80', name='ck_classroom_reporting_notice_version'),
    sa.CheckConstraint('length(trim(scope_version)) BETWEEN 1 AND 80', name='ck_classroom_reporting_scope_version'),
    sa.ForeignKeyConstraint(['authorizer_profile_id'], ['woodchuck_profiles.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['class_id', 'program_id'], ['classroom_classes.id', 'classroom_classes.program_id'], name='fk_classroom_reporting_class', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['consent_id'], ['child_consent_evidence.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['ended_by_profile_id'], ['woodchuck_profiles.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['entitlement_id', 'program_id'], ['classroom_entitlements.id', 'classroom_entitlements.program_id'], name='fk_classroom_reporting_entitlement', ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['membership_id'], ['classroom_student_memberships.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['membership_period_id'], ['classroom_membership_periods.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['profile_id'], ['woodchuck_profiles.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_audit_id'], ['classroom_s2_audit_events.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('id', 'program_id', 'class_id', 'profile_id', name='uq_classroom_reporting_scope')
    )
    op.create_index('ix_classroom_reporting_student_class', 'classroom_reporting_periods', ['profile_id', 'class_id'], unique=False)
    op.create_index('uq_classroom_reporting_open', 'classroom_reporting_periods', ['membership_id'], unique=True, sqlite_where=sa.text('ended_at IS NULL'), postgresql_where=sa.text('ended_at IS NULL'))
    op.create_table('classroom_s3_audit_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('program_id', sa.Integer(), nullable=False),
    sa.Column('class_id', sa.Integer(), nullable=False),
    sa.Column('profile_id', sa.Integer(), nullable=False),
    sa.Column('period_id', sa.Integer(), nullable=False),
    sa.Column('actor_profile_id', sa.Integer(), nullable=False),
    sa.Column('action', sa.String(length=30), nullable=False),
    sa.Column('scope_version', sa.String(length=80), nullable=False),
    sa.Column('notice_version', sa.String(length=80), nullable=False),
    sa.Column('reason', sa.String(length=30), nullable=True),
    sa.Column('occurred_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.CheckConstraint("(action = 'reporting_withdrawn' AND reason IS NOT NULL AND reason IN ('withdrawn', 'source_changed')) OR (action <> 'reporting_withdrawn' AND reason IS NULL)", name='ck_classroom_s3_audit_reason'),
    sa.CheckConstraint("action IN ('reporting_granted', 'reporting_withdrawn', 'reporting_reauthorized')", name='ck_classroom_s3_audit_action'),
    sa.CheckConstraint('actor_profile_id = profile_id', name='ck_classroom_s3_audit_actor'),
    sa.CheckConstraint('length(trim(notice_version)) BETWEEN 1 AND 80', name='ck_classroom_s3_audit_notice_version'),
    sa.CheckConstraint('length(trim(scope_version)) BETWEEN 1 AND 80', name='ck_classroom_s3_audit_scope_version'),
    sa.ForeignKeyConstraint(['actor_profile_id'], ['woodchuck_profiles.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['period_id', 'program_id', 'class_id', 'profile_id'], ['classroom_reporting_periods.id', 'classroom_reporting_periods.program_id', 'classroom_reporting_periods.class_id', 'classroom_reporting_periods.profile_id'], name='fk_classroom_s3_audit_scope', ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_classroom_s3_audit_program_time', 'classroom_s3_audit_events', ['program_id', 'occurred_at'], unique=False)
    _install_reporting_guards()


SQLITE_REPORTING_INSERT = """CREATE TRIGGER classroom_reporting_no_overlap_insert
BEFORE INSERT ON classroom_reporting_periods
FOR EACH ROW WHEN EXISTS (
    SELECT 1 FROM classroom_reporting_periods p
    WHERE p.membership_id = NEW.membership_id
      AND (NEW.ended_at IS NULL OR p.starts_at < NEW.ended_at)
      AND (p.ended_at IS NULL OR NEW.starts_at < p.ended_at)
)
BEGIN SELECT RAISE(ABORT, 'classroom_reporting_period_overlap'); END"""

SQLITE_REPORTING_UPDATE = """CREATE TRIGGER classroom_reporting_no_overlap_update
BEFORE UPDATE ON classroom_reporting_periods
FOR EACH ROW WHEN EXISTS (
    SELECT 1 FROM classroom_reporting_periods p
    WHERE p.membership_id = NEW.membership_id AND p.id <> NEW.id
      AND (NEW.ended_at IS NULL OR p.starts_at < NEW.ended_at)
      AND (p.ended_at IS NULL OR NEW.starts_at < p.ended_at)
)
BEGIN SELECT RAISE(ABORT, 'classroom_reporting_period_overlap'); END"""

POSTGRES_REPORTING_FUNCTION = """CREATE FUNCTION classroom_reporting_no_overlap() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        -- PostgreSQL locks an UPDATE target tuple before BEFORE ROW triggers.
        -- Never wait for its parents in that state: supported writers already
        -- hold these locks; uncoordinated DML must fail instead of deadlocking.
        PERFORM organization_id FROM classroom_programs
        WHERE organization_id = NEW.program_id FOR UPDATE NOWAIT;
        PERFORM id FROM classroom_classes
        WHERE id = NEW.class_id AND program_id = NEW.program_id FOR UPDATE NOWAIT;
        PERFORM id FROM classroom_student_memberships
        WHERE id = NEW.membership_id FOR UPDATE NOWAIT;
    ELSE
        PERFORM organization_id FROM classroom_programs
        WHERE organization_id = NEW.program_id FOR UPDATE;
        PERFORM id FROM classroom_classes
        WHERE id = NEW.class_id AND program_id = NEW.program_id FOR UPDATE;
        PERFORM id FROM classroom_student_memberships
        WHERE id = NEW.membership_id FOR UPDATE;
    END IF;
    IF EXISTS (
        SELECT 1 FROM classroom_reporting_periods p
        WHERE p.membership_id = NEW.membership_id AND p.id <> NEW.id
          AND (NEW.ended_at IS NULL OR p.starts_at < NEW.ended_at)
          AND (p.ended_at IS NULL OR NEW.starts_at < p.ended_at)
    ) THEN
        RAISE EXCEPTION 'classroom_reporting_period_overlap' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$"""

POSTGRES_REPORTING_TRIGGER = """CREATE TRIGGER classroom_reporting_no_overlap
BEFORE INSERT OR UPDATE ON classroom_reporting_periods
FOR EACH ROW EXECUTE FUNCTION classroom_reporting_no_overlap()"""


def _install_reporting_guards():
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(POSTGRES_REPORTING_FUNCTION)
        op.execute(POSTGRES_REPORTING_TRIGGER)
    elif dialect == "sqlite":
        op.execute(SQLITE_REPORTING_INSERT)
        op.execute(SQLITE_REPORTING_UPDATE)
    else:
        raise RuntimeError("classroom_unsupported_database")


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("LOCK TABLE classroom_programs, classroom_classes, "
            "classroom_student_memberships, classroom_reporting_periods, "
            "classroom_s3_audit_events IN ACCESS EXCLUSIVE MODE"))
    elif bind.dialect.name == "sqlite":
        bind.execute(sa.text("UPDATE classroom_programs SET owner_version = owner_version WHERE 0"))
    for table in S3_TABLES:
        if bind.scalar(sa.text(f"SELECT count(*) FROM {table}")):
            raise RuntimeError("classroom_s3_reporting_history_in_use: downgrade refused")
    if bind.dialect.name == "postgresql":
        op.execute("DROP TRIGGER classroom_reporting_no_overlap ON classroom_reporting_periods")
        op.execute("DROP FUNCTION classroom_reporting_no_overlap()")
    elif bind.dialect.name == "sqlite":
        op.execute("DROP TRIGGER classroom_reporting_no_overlap_insert")
        op.execute("DROP TRIGGER classroom_reporting_no_overlap_update")
    for table in reversed(S3_TABLES):
        op.drop_table(table)
