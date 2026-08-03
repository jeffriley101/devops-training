"""add account lifecycle and team moderation

Revision ID: c91f2d3e4a50
Revises: b84f0c29e173
Create Date: 2026-08-03
"""

from alembic import op
import sqlalchemy as sa


revision = "c91f2d3e4a50"
down_revision = "b84f0c29e173"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("woodchuck_profiles") as batch:
        batch.add_column(sa.Column("status", sa.String(20), server_default="active", nullable=False))
        batch.add_column(sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("retired_woodchuck_id_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("session_version", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("deletion_failed_attempts", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("deletion_last_failed_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint("ck_woodchuck_profile_status", "status IN ('active', 'deleted')")
        batch.create_unique_constraint("uq_woodchuck_profiles_retired_id_hash", ["retired_woodchuck_id_hash"])
        batch.create_index("ix_woodchuck_profiles_status", ["status"])
        batch.create_index("ix_woodchuck_profiles_deleted_at", ["deleted_at"])

    with op.batch_alter_table("teams") as batch:
        batch.add_column(sa.Column("moderation_status", sa.String(20), server_default="active", nullable=False))
        batch.add_column(sa.Column("moderation_updated_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_check_constraint(
            "ck_team_moderation_status",
            "moderation_status IN ('active', 'under_review', 'hidden')",
        )
        batch.create_index("ix_teams_moderation_status", ["moderation_status"])

    op.create_table(
        "team_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("reporter_profile_id", sa.Integer(), nullable=True),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("details", sa.String(500), nullable=True),
        sa.Column("status", sa.String(20), server_default="unresolved", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reporter_profile_id"], ["woodchuck_profiles.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "category IN ('inappropriate_name', 'inappropriate_emblem', 'impersonation', 'other')",
            name="ck_team_report_category",
        ),
        sa.CheckConstraint(
            "status IN ('unresolved', 'dismissed', 'actioned')",
            name="ck_team_report_status",
        ),
    )
    op.create_index("ix_team_reports_team_id", "team_reports", ["team_id"])
    op.create_index("ix_team_reports_reporter_profile_id", "team_reports", ["reporter_profile_id"])
    op.create_index("ix_team_reports_category", "team_reports", ["category"])
    op.create_index("ix_team_reports_status", "team_reports", ["status"])
    op.create_index(
        "uq_team_report_unresolved_reporter_team", "team_reports",
        ["reporter_profile_id", "team_id"], unique=True,
        sqlite_where=sa.text("status = 'unresolved'"),
        postgresql_where=sa.text("status = 'unresolved'"),
    )


def downgrade() -> None:
    op.drop_table("team_reports")
    with op.batch_alter_table("teams") as batch:
        batch.drop_index("ix_teams_moderation_status")
        batch.drop_constraint("ck_team_moderation_status", type_="check")
        batch.drop_column("moderation_updated_at")
        batch.drop_column("moderation_status")
    with op.batch_alter_table("woodchuck_profiles") as batch:
        batch.drop_index("ix_woodchuck_profiles_deleted_at")
        batch.drop_index("ix_woodchuck_profiles_status")
        batch.drop_constraint("uq_woodchuck_profiles_retired_id_hash", type_="unique")
        batch.drop_constraint("ck_woodchuck_profile_status", type_="check")
        batch.drop_column("deletion_last_failed_at")
        batch.drop_column("deletion_failed_attempts")
        batch.drop_column("session_version")
        batch.drop_column("retired_woodchuck_id_hash")
        batch.drop_column("deleted_at")
        batch.drop_column("status")
