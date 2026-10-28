"""Retain student-specific invitation evidence; no enrollment or access grants."""
from alembic import op
import sqlalchemy as sa

revision = "c26class001"
down_revision = "c25class001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "classroom_student_invitations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("program_id", sa.Integer(), nullable=False),
        sa.Column("class_id", sa.Integer(), nullable=False),
        sa.Column("assignment_id", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.Integer(), sa.ForeignKey("woodchuck_profiles.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("issued_by_verifier_id", sa.Integer(), sa.ForeignKey("trusted_verifiers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("accepted_by_profile_id", sa.Integer(), sa.ForeignKey("woodchuck_profiles.id", ondelete="RESTRICT")),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_by_verifier_id", sa.Integer(), sa.ForeignKey("trusted_verifiers.id", ondelete="RESTRICT")),
        sa.ForeignKeyConstraint(["assignment_id", "class_id", "program_id"],
            ["classroom_teaching_assignments.id", "classroom_teaching_assignments.class_id",
             "classroom_teaching_assignments.program_id"],
            ondelete="RESTRICT", name="fk_classroom_student_invitation_assignment"),
        sa.CheckConstraint("expires_at > issued_at", name="ck_classroom_student_invitation_expiry"),
        sa.CheckConstraint(
            "(accepted_at IS NULL AND accepted_by_profile_id IS NULL) OR "
            "(accepted_at IS NOT NULL AND accepted_by_profile_id IS NOT NULL AND "
            "accepted_by_profile_id = profile_id AND accepted_at >= issued_at AND accepted_at < expires_at)",
            name="ck_classroom_student_invitation_acceptance"),
        sa.CheckConstraint(
            "(revoked_at IS NULL AND revoked_by_verifier_id IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoked_by_verifier_id IS NOT NULL AND "
            "revoked_by_verifier_id = issued_by_verifier_id AND revoked_at >= issued_at AND "
            "(accepted_at IS NULL OR revoked_at >= accepted_at))",
            name="ck_classroom_student_invitation_revocation"),
    )
    op.create_index("ix_classroom_student_invitation_recipient", "classroom_student_invitations",
                    ["profile_id", "assignment_id"])


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(sa.text("LOCK TABLE classroom_student_invitations IN ACCESS EXCLUSIVE MODE"))
    elif bind.dialect.name == "sqlite":
        bind.execute(sa.text("UPDATE classroom_student_invitations SET id = id WHERE 0"))
    else:
        raise RuntimeError("classroom_student_invitations_unsupported_database")
    if bind.scalar(sa.text("SELECT count(*) FROM classroom_student_invitations")):
        raise RuntimeError("classroom_student_invitations_in_use: downgrade refused")
    op.drop_index("ix_classroom_student_invitation_recipient", table_name="classroom_student_invitations")
    op.drop_table("classroom_student_invitations")
