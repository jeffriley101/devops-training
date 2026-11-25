"""Preserve Classroom enforcement while disposing of obsolete attribution."""
from alembic import op

revision = "c38disp001"
down_revision = "c37hold001"
branch_labels = None
depends_on = None


def upgrade():
    from app import classroom_residual

    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        # The supervised disposition service requires PostgreSQL roles. Keep
        # the existing stricter SQLite schema and earlier local entry behavior.
        return
    if bind.dialect.name != "postgresql" or bind.exec_driver_sql("SELECT current_schema()").scalar_one() != "public":
        raise RuntimeError("C38 requires the PostgreSQL public application schema.")
    op.execute("""
        LOCK TABLE public.classroom_programs, public.classroom_class_states,
            public.classroom_entry_codes, public.classroom_teaching_assignments,
            public.classroom_audit_events, public.classroom_s2_audit_events IN ACCESS EXCLUSIVE MODE
    """)
    op.execute("""
        ALTER TABLE public.classroom_student_memberships ADD COLUMN reporting_form_revision integer NOT NULL DEFAULT 0,
            ADD CONSTRAINT ck_c38_form_revision CHECK (reporting_form_revision >= 0);
        ALTER TABLE public.classroom_programs ADD COLUMN retired_teaching_units integer NOT NULL DEFAULT 0,
            ADD COLUMN retired_allocation_bound integer NOT NULL DEFAULT 0,
            ADD CONSTRAINT ck_c38_retired_allocation_bound CHECK (retired_allocation_bound >= 0),
            ADD CONSTRAINT ck_c38_retired_teaching_units CHECK (retired_teaching_units >= 0);
        ALTER TABLE public.classroom_class_states ADD COLUMN entry_generation integer NOT NULL DEFAULT 0,
            ADD CONSTRAINT ck_c38_entry_generation CHECK (entry_generation >= 0);
        UPDATE public.classroom_class_states AS state
           SET entry_generation = history.highest
          FROM (
            SELECT program_id, class_id, max(generation) AS highest
              FROM public.classroom_entry_codes GROUP BY program_id, class_id
          ) AS history
         WHERE state.program_id = history.program_id AND state.class_id = history.class_id;
    """)
    if bind.exec_driver_sql("""
        SELECT EXISTS (
            SELECT 1 FROM public.classroom_entry_codes AS code
            LEFT JOIN public.classroom_class_states AS state
              ON state.class_id = code.class_id AND state.program_id = code.program_id
            WHERE state.class_id IS NULL
        )
    """).scalar_one():
        raise RuntimeError("C38 entry history has no exact Class state.")
    op.execute("""
        ALTER TABLE public.classroom_class_states ALTER COLUMN changed_by_verifier_id DROP NOT NULL,
            ADD CONSTRAINT ck_c38_class_attribution CHECK (state = 'archived' OR changed_by_verifier_id IS NOT NULL);
        ALTER TABLE public.classroom_audit_events ALTER COLUMN actor_verifier_id DROP NOT NULL,
            DROP CONSTRAINT ck_classroom_audit_provision_evidence,
            DROP CONSTRAINT ck_classroom_audit_role_evidence,
            DROP CONSTRAINT ck_classroom_audit_teacher_evidence,
            ADD CONSTRAINT ck_classroom_audit_provision_evidence CHECK (
                actor_verifier_id IS NULL OR action <> 'program_provisioned'
                OR (new_owner_id IS NOT NULL AND target_verifier_id IS NOT NULL)),
            ADD CONSTRAINT ck_classroom_audit_role_evidence CHECK (
                actor_verifier_id IS NULL OR action NOT IN ('role_granted','role_revoked')
                OR (role_grant_id IS NOT NULL AND target_verifier_id IS NOT NULL)),
            ADD CONSTRAINT ck_classroom_audit_teacher_evidence CHECK (
                action NOT IN ('teacher_assigned','teacher_revoked','teacher_departed')
                OR (class_id IS NOT NULL AND (actor_verifier_id IS NULL
                    OR (assignment_id IS NOT NULL AND target_verifier_id IS NOT NULL))));
        ALTER TABLE public.classroom_s2_audit_events DROP CONSTRAINT ck_classroom_s2_audit_actor,
            ADD CONSTRAINT ck_classroom_s2_audit_actor CHECK (
                num_nonnulls(actor_verifier_id, actor_profile_id, actor_admin) <= 1);
    """)
    classroom_residual.install(bind)
    classroom_residual.validate(bind, RuntimeError)


def downgrade():
    if op.get_bind().dialect.name != "sqlite":
        # Some original rows and actor links may already be gone. The pre-C38
        # application cannot safely interpret retired generation or capacity.
        raise RuntimeError("C38 downgrade refused: disposition is forward-only. Keep recovery quarantined.")
