"""Add durable tester enrollment and pending-consent cohort claims."""

from alembic import op
import sqlalchemy as sa


revision = "b14tester001"
down_revision = "a13screen004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "tester_enrollments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "profile_id",
            sa.Integer(),
            sa.ForeignKey("woodchuck_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cohort_key", sa.String(40), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "length(cohort_key) >= 1",
            name="ck_tester_enrollment_cohort_key",
        ),
        sa.UniqueConstraint(
            "profile_id",
            "cohort_key",
            name="uq_tester_enrollment_profile_cohort",
        ),
    )
    op.create_index(
        "ix_tester_enrollments_profile_id",
        "tester_enrollments",
        ["profile_id"],
    )
    op.create_index(
        "ix_tester_enrollments_cohort_key",
        "tester_enrollments",
        ["cohort_key"],
    )
    op.add_column(
        "child_pending_consents",
        sa.Column("cohort_key", sa.String(40), nullable=True),
    )
    op.add_column(
        "child_pending_consents",
        sa.Column("cohort_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade():
    op.drop_column("child_pending_consents", "cohort_claimed_at")
    op.drop_column("child_pending_consents", "cohort_key")
    op.drop_index("ix_tester_enrollments_cohort_key", table_name="tester_enrollments")
    op.drop_index("ix_tester_enrollments_profile_id", table_name="tester_enrollments")
    op.drop_table("tester_enrollments")
